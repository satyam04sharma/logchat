"""Controlled admission only; never installed assets, HTTP or real tokenizers."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from logchat.rag.contracts import EmbeddingSpec
from logchat.rag.embeddings import OllamaEmbeddingProvider
from logchat.rag.embedding_subdivision import (
    plan_embedding_subdivision, validate_embedding_plan, record_subset,
)
from logchat.rag.sections import embedding_text, validate_compact_chunk
from pipeline.native_tokenizer.ollama import EmbeddingTokenCount
from pipeline.token_budget import GenerationTokenIdentity
from pipeline.model_transport import model_json
from tests.test_embedding_subdivision import fixture


class Counter:
    def __init__(self, measure):
        self.identity = GenerationTokenIdentity('nomic-embed-text', 'a' * 64, 'b' * 64,
            hashlib.sha256(b'prefix-v1').hexdigest(), '0.34.4')
        self.measure = measure
        self.calls = []
        self.closed = False
        self.verified = 0
        self.mutate = lambda proof: proof

    async def count(self, request):
        assert not self.closed
        self.calls.append(request)
        body = json.loads(request)
        assert body['truncate'] is False
        assert all(text.startswith('search_document: ') for text in body['input'])
        counts = tuple(self.measure(text) for text in body['input'])
        return self.mutate(EmbeddingTokenCount(self.identity, hashlib.sha256(request).hexdigest(),
                                               tuple(n - 2 for n in counts), counts))

    async def verify_provider(self):
        self.verified += 1


class Models:
    base_url = 'http://127.0.0.1:11434'
    transport = None
    embedding_model = 'nomic-embed-text'

    def __init__(self, counter):
        self.counter = counter
        self.sessions = 0
        self.deadlines = []

    @asynccontextmanager
    async def embedding_token_session(self, *, deadline):
        self.sessions += 1
        self.deadlines.append(deadline)
        try:
            yield self.counter
        finally:
            self.counter.closed = True


def setup(measure=lambda text: 2048):
    counter = Counter(measure)
    models = Models(counter)
    provider = OllamaEmbeddingProvider(spec=EmbeddingSpec('ollama', 'nomic-embed-text',
        'a' * 64 + ':prefix-v1', 768), preserve_content=True)
    return provider, models, counter


async def plan(original, provider, models, seconds=10):
    return await plan_embedding_subdivision(original, provider, models,
                                            deadline=asyncio.get_running_loop().time() + seconds)


@pytest.mark.asyncio
async def test_exact_edge_preserves_parent_and_complete_wire_bytes():
    original = fixture()
    provider, models, counter = setup()
    result = await plan(original, provider, models)
    assert result.batch.chunks[0] is original.chunks[0]
    assert result.lineage.original_payload_sha256 == result.lineage.final_payload_sha256
    assert counter.closed and counter.verified == models.sessions == 1
    assert result.documents[0].physical_tokens == 2048
    assert result.requests[0].proof.frontend_tokens == (2046,)
    assert validate_embedding_plan(original, result, provider) == result.lineage
    sent = []
    def handler(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': provider.spec.model, 'digest': 'a' * 64}]})
        sent.append(request.content)
        return httpx.Response(200, json={'embeddings': [[1.] * 768]})
    provider.transport = httpx.MockTransport(handler)
    await provider.embed([embedding_text(c) for c in result.batch.chunks])
    assert sent == [counter.calls[-1]]


@pytest.mark.asyncio
async def test_midpoints_no_monotonicity_and_typed_phrase_conservation():
    original = fixture()
    # 6→3+3; the right 3 is MORE costly than its parent. Both need counting.
    inputs = {embedding_text(record_subset(original.chunks[0], a, b)): count
              for a, b, count in [(0, 6, 2049), (0, 3, 2048), (3, 6, 2200),
                                  (3, 4, 2048), (4, 6, 2048)]}
    provider, models, counter = setup(lambda text: inputs[text.removeprefix('search_document: ')])
    result = await plan(original, provider, models)
    assert [(leaf.record_start, leaf.record_end) for leaf in result.lineage.leaves] == [(0, 3), (3, 4), (4, 6)]
    assert len(counter.calls) == 6  # five tree nodes plus the exact final wire batch
    assert result.requests[0].proof.physical_tokens == (2048, 2048, 2048)
    assert validate_compact_chunk(result.batch.chunks[1])['source_observations'] == []
    validate_embedding_plan(original, result, provider)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['hash', 'identity', 'context', 'dimensions', 'prefix', 'bool', 'shape', 'zero', 'front'])
async def test_counter_proof_corruption_closes_owner(mutation):
    provider, models, counter = setup()
    def mutate(proof):
        updates = {'hash': {'request_sha256': '0' * 64},
            'identity': {'identity': replace(proof.identity, tokenizer_digest='c' * 64)},
            'context': {'context_tokens': 2049}, 'dimensions': {'dimensions': 1},
            'prefix': {'prefix_version': 'prefix-v2'}, 'bool': {'physical_tokens': (True,)},
            'shape': {'physical_tokens': ()}, 'zero': {'physical_tokens': (0,)},
            'front': {'frontend_tokens': (False,)}}
        return replace(proof, **updates[mutation])
    counter.mutate = mutate
    with pytest.raises(ValueError, match='proof_mismatch'):
        await plan(fixture(), provider, models)
    assert counter.closed and len(counter.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['singleton', 'late_count', 'cancel', 'deadline', 'model', 'transport', 'spec', 'late_child'])
async def test_whole_plan_failure_closes_scope(failure, monkeypatch):
    original = fixture()
    provider, models, counter = setup(lambda text: 2049 if failure == 'singleton' else 2048)
    real_count = counter.count
    async def count(request):
        proof = await real_count(request)
        if failure == 'late_count' and len(counter.calls) == 2:
            raise RuntimeError('controlled counter failure')
        if failure == 'cancel':
            raise asyncio.CancelledError
        if failure == 'deadline':
            raise TimeoutError
        if failure == 'model':
            models.embedding_model = 'other'
        if failure == 'transport':
            models.transport = object()
        if failure == 'spec':
            provider.spec = replace(provider.spec, dimensions=767)
        return proof
    counter.count = count
    if failure == 'late_child':
        import logchat.rag.embedding_subdivision as module
        counter.measure = lambda text: 2049
        subset = module.record_subset
        def invalid(parent, start, end):
            if start == 1:
                raise ValueError('invalid late child')
            return subset(parent, start, end)
        monkeypatch.setattr(module, 'record_subset', invalid)
        counter.measure = lambda text: 2049 if len(json.loads(text[17:])['important_fields']) > 1 else 2048
    with pytest.raises((ValueError, RuntimeError, asyncio.CancelledError, TimeoutError)):
        await plan(original, provider, models)
    assert counter.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['flags', 'document', 'request', 'lineage', 'missing', 'extra', 'revision'])
async def test_strict_final_manifest_rejects_arbitrary_alteration(mutation):
    provider, models, _ = setup()
    original = fixture()
    result = await plan(original, provider, models)
    updates = {'flags': {'physical_flags': (False, True)},
        'document': {'documents': (replace(result.documents[0], input_sha256='0' * 64),)},
        'request': {'requests': (replace(result.requests[0], first_document=1),)},
        'lineage': {'lineage': replace(result.lineage, operation_version='unknown')},
        'missing': {'documents': ()}, 'extra': {'requests': result.requests * 2},
        'revision': {'spec': replace(result.spec, revision='other')}}
    with pytest.raises(ValueError):
        validate_embedding_plan(original, replace(result, **updates[mutation]), provider)


@pytest.mark.asyncio
async def test_payload_cap_and_expired_deadline_do_not_open_counter():
    original = fixture()
    oversized = replace(original, chunks=(replace(original.chunks[0], loss_notes=('雪' * 500_001,)),))
    provider, models, _ = setup()
    with pytest.raises(ValueError, match='payload_limit'):
        await plan(oversized, provider, models)
    assert models.sessions == 0
    with pytest.raises(TimeoutError):
        await plan_embedding_subdivision(original, provider, models, deadline=asyncio.get_running_loop().time() - 1)
    assert models.sessions == 0


# Imports this controlled fixture only; it builds an inert C library in tmp_path.
from tests.test_default_native_counter import installation, reaped


@pytest.mark.asyncio
async def test_default_profile_planner_owns_one_controlled_ipc_worker(installation, monkeypatch):
    client, paths, calls, _, profile = installation
    from pipeline.native_tokenizer import ollama
    digest = ollama.MODELS['nomic-embed-text:latest'][0]
    profile.config['embedding'].update(provider='ollama', revision=digest + ':prefix-v1')
    owners = []
    actual_session = client.embedding_token_session
    @asynccontextmanager
    async def observed(*, deadline):
        async with actual_session(deadline=deadline) as counter:
            owners.append(counter)
            yield counter
    monkeypatch.setattr(client, 'embedding_token_session', observed)
    deadline = asyncio.get_running_loop().time() + 15
    original = fixture()
    from tests.test_embedding_subdivision import resign
    evidence = validate_compact_chunk(original.chunks[0])
    for field in evidence['important_fields']:
        field['key'] = 'typed' + 'x' * 180
    original = replace(original, chunks=(resign(original.chunks[0], evidence),))
    result = await profile.plan_embedding_batch(original, deadline=deadline)
    assert len(owners) == 1 and owners[0].deadline == deadline
    reaped(owners[0])
    assert result.lineage.original_payload_sha256 != result.lineage.final_payload_sha256
    assert all(d.physical_tokens == d.input_bytes + 3 <= 2048 for d in result.documents)
    assert all(request.proof.identity == result.identity for request in result.requests)
    assert [endpoint for endpoint, body in calls] == ['/api/version', '/api/tags', '/api/show'] * 2
    audit = paths['nomic-embed-text:latest'].with_name(paths['nomic-embed-text:latest'].name + '.audit').read_text()
    assert audit == 'open\nfree\n'
    assert not any(endpoint in {'/api/embed', '/api/generate'} for endpoint, _ in calls)


def many_parents(count):
    from logchat.rag.builder import event_key
    from logchat.rag.sections import record_metrics
    from tests.test_embedding_subdivision import resign
    original = fixture()
    chunks, records = [], []
    for index in range(count):
        evidence = validate_compact_chunk(original.chunks[0])
        for row in evidence['records']:
            row['event_id'] = f"parent-{index}-{row['event_id']}"
            row['event_key'] = event_key(original.identity, row['event_id'])
        records.extend(evidence['records'])
        chunks.append(resign(original.chunks[0], evidence))
    return replace(original, chunks=tuple(chunks), event_keys=tuple(r['event_key'] for r in records),
                   coverage=tuple(replace(c, metrics=record_metrics(records)) for c in original.coverage))


@pytest.mark.asyncio
async def test_every_final_provider_batch_including_remainder_is_bound():
    original = many_parents(19)
    provider, models, counter = setup()
    result = await plan(original, provider, models)
    assert [(r.first_document, r.document_count) for r in result.requests] == [(0, 16), (16, 3)]
    assert len(result.documents) == 19
    assert [len(json.loads(call)['input']) for call in counter.calls] == [1] * 19 + [16, 3]
    assert result.batch == original
    with pytest.raises(ValueError):
        validate_embedding_plan(original, replace(result, requests=result.requests[:1]), provider)


@pytest.mark.asyncio
async def test_actual_500_chunk_cap_rejects_whole_plan():
    original = many_parents(84)  # 504 records, 84 valid original parents
    def measure(text):
        n = json.loads(text.removeprefix('search_document: '))['whole_section_facts']['exact_metrics']['event_count']
        return 2049 if n > 1 else 2048
    provider, models, counter = setup(measure)
    # CPU-heavy 504-record plan: slower CI runners need more than the 10 s
    # default to reach the batch-limit rejection this test asserts.
    with pytest.raises(ValueError, match='subdivision_batch_limit'):
        await plan(original, provider, models, seconds=120)
    assert counter.closed
    assert counter.verified == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['compact-evidence-v1', 'compact-evidence-v2'])
async def test_legacy_fitting_batch_and_recipe_are_exact_noop(version):
    from tests.test_compact_embedding_versions import fixture_chunk
    from tests.test_rag_sections import batch, event
    chunk = fixture_chunk(version=version)
    original = replace(batch([event()]), chunks=(chunk,))
    before = embedding_text(chunk)
    provider, models, _ = setup()
    result = await plan(original, provider, models)
    assert result.batch.chunks[0] is chunk
    assert embedding_text(result.batch.chunks[0]) == before
    assert result.lineage.original_payload_sha256 == result.lineage.final_payload_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['base_url', 'transport', 'spec', 'preserve_content'])
async def test_provider_drift_during_revision_check_prevents_embedding_post(field):
    provider, _, _ = setup()
    posts = []
    async def verify(client):
        setattr(provider, field, {'base_url': 'http://other.invalid', 'transport': object(),
            'spec': replace(provider.spec, revision='changed'), 'preserve_content': False}[field])
    provider._verify_revision = verify
    async def request(*args, **kwargs):
        posts.append(kwargs)
        raise AssertionError('must not send')
    provider._request = request
    from pipeline.models import ModelUnavailable
    with pytest.raises(ModelUnavailable):
        await provider.embed(['exact document'])
    assert posts == []


@pytest.mark.asyncio
async def test_subdivision_expansion_crossing_3mb_rejects_before_final_requests():
    original = fixture()
    original = replace(original, chunks=(replace(original.chunks[0], loss_notes=('x' * 800_000,)),))
    def measure(text):
        n = json.loads(text.removeprefix('search_document: '))['whole_section_facts']['exact_metrics']['event_count']
        return 2049 if n > 1 else 2048
    provider, models, counter = setup(measure)
    with pytest.raises(ValueError, match='subdivision_payload_limit'):
        await plan(original, provider, models)
    assert counter.closed and counter.verified == 0
    assert all(len(json.loads(call)['input']) == 1 for call in counter.calls)


@pytest.mark.asyncio
async def test_default_planner_cancellation_reaps_controlled_owner(installation, monkeypatch):
    client, _, _, _, profile = installation
    from pipeline.native_tokenizer import ollama
    profile.config['embedding'].update(provider='ollama',
        revision=ollama.MODELS['nomic-embed-text:latest'][0] + ':prefix-v1')
    entered = asyncio.Event()
    owners = []
    actual_session = client.embedding_token_session
    @asynccontextmanager
    async def observed(*, deadline):
        async with actual_session(deadline=deadline) as counter:
            owners.append(counter)
            actual_verify = counter.verify_provider
            async def verify():
                if counter.runtime is not None:
                    entered.set()
                    await asyncio.Event().wait()
                await actual_verify()
            counter.verify_provider = verify
            yield counter
    monkeypatch.setattr(client, 'embedding_token_session', observed)
    task = asyncio.create_task(profile.plan_embedding_batch(fixture(),
        deadline=asyncio.get_running_loop().time() + 15))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(owners) == 1
    reaped(owners[0])
