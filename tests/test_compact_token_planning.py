"""Controlled counting tests, not measurements of the native tokenizer."""
from logchat.rag.compact_input import expand_compact_input
import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, SectionModelError, SummaryConfig,
    _pack_compact_physical, compact_summary_batch, records_for_chunk, validate_refinement,
)
from pipeline.model_transport import model_json, SYSTEM_SAFETY_SUFFIX
from pipeline.models import LocalModels, ModelUnavailable
from pipeline.token_budget import GenerationTokenCount, GenerationTokenIdentity, request_digest
from tests.test_catalog_compact_preflight import fixture
from tests.test_rag_compact import CompactModel

IDENTITY = GenerationTokenIdentity('mistral:7b', 'a' * 64, 'b' * 64, 'c' * 64, '0.34.4')


class Counter:
    identity = IDENTITY

    def __init__(self, tokens=6992):
        self.tokens, self.requests = tokens, []

    async def count(self, request_bytes):
        body = json.loads(request_bytes)
        self.requests.append(body)
        return GenerationTokenCount(self.identity, request_digest(body), self.tokens)


@pytest.mark.asyncio
async def test_complete_request_boundary_and_identical_generation_wire_bytes():
    sent = []

    def handler(request):
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'}, 'capabilities': ['completion']})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'response': '{}'})

    counter = Counter()
    model = LocalModels(base_url='http://localhost:11434', chat_model='mistral:7b', preserve_content=True,
                        generation_token_counter=counter, transport=httpx.MockTransport(handler))
    context = {'refs': list(range(100)), 'escaped': '\\"\n😀é'}
    schema = {'type': 'object', 'properties': {'choice': {'enum': [f'f{i}' for i in range(1000)]}}}
    assert await model.preflight_generation('instruction', context, schema)
    assert await model.generate('instruction', context, schema) == {}
    assert counter.requests[0] == counter.requests[1] == sent[0]
    assert sent[0]['system'] == 'instruction' + SYSTEM_SAFETY_SUFFIX
    assert sent[0]['prompt'] == model_json(context)
    assert sent[0]['format'] == schema
    counter.tokens = 6993
    assert not await model.preflight_generation('instruction', context, schema)
    with pytest.raises(ModelUnavailable):
        await model.generate('instruction', context, schema)
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['hash', 'identity', 'context', 'reserve', 'boolean', 'negative', 'stale'])
async def test_invalid_proof_is_content_free_and_not_a_split_signal(mutation):
    class Invalid(Counter):
        async def count(self, request_bytes):
            proof = await super().count(request_bytes)
            if mutation == 'stale':
                self.identity = replace(IDENTITY, renderer_digest='d' * 64)
                return proof
            return replace(proof, **{
                'hash': {'request_sha256': 'd' * 64},
                'identity': {'identity': replace(IDENTITY, model_digest='d' * 64)},
                'context': {'context_tokens': 8193}, 'reserve': {'output_reserve': 1199},
                'boolean': {'input_tokens': True}, 'negative': {'input_tokens': -1},
            }[mutation])
    model = LocalModels(chat_model='mistral:7b', generation_token_counter=Invalid())
    with pytest.raises(ModelUnavailable, match='^generation_token_proof_unavailable$'):
        await model.preflight_generation('PRIVATE', {}, {})


@pytest.mark.asyncio
async def test_missing_adapter_fails_without_any_provider_request():
    def forbidden(request):
        pytest.fail('Unexpected provider request')
    model = LocalModels(transport=httpx.MockTransport(forbidden))
    with pytest.raises(ModelUnavailable, match='generation_token_capability_unavailable'):
        await model.preflight_generation('instruction', {}, {})


class SizedModel(CompactModel):
    """An explicit synthetic partition predicate; no native token claim."""
    def __init__(self, limit=2, bad_key=None):
        super().__init__('empty_fields')
        self.limit, self.bad_key, self.checked = limit, bad_key, []

    async def preflight_generation(self, instruction, payload, schema):
        assert self.calls == 0
        assert instruction == CATALOG_COMPACT_INSTRUCTION
        assert schema['properties']['selections']['items']['properties']['phrase_ref']['enum'] == [p[0] for p in payload['phrase_catalog']]
        keys = tuple(r['timestamp'] for r in expand_compact_input(payload)['records'])
        self.checked.append(keys)
        return len(keys) <= self.limit and not (len(keys) == 1 and keys[0] == self.bad_key)


@pytest.mark.asyncio
async def test_plan_checks_final_requests_preserving_exact_records():
    source, views, options = await fixture(['operation completed after delay'] * 7)
    model = SizedModel()
    packed = await _pack_compact_physical(source, model, SummaryConfig(), views, **options)
    original = [r for c in source.chunks for r in records_for_chunk(c)]
    retained = [r for c in packed.chunks for r in records_for_chunk(c)]
    assert retained == original
    assert [len(records_for_chunk(c)) for c in packed.chunks] == [1, 2, 2, 2]
    assert [len(keys) for keys in model.checked] == [7, 3, 1, 2, 4, 2, 2]
    assert all(tuple(r['timestamp'] for r in records_for_chunk(c)) in model.checked for c in packed.chunks)
    assert len(model.checked) == len(set(model.checked))
    assert len(model.checked) <= 2 * len(original) - len(source.chunks)
    assert all(c.loss_notes == source.chunks[0].loss_notes for c in packed.chunks)
    validate_refinement(source, packed)
    compact = await compact_summary_batch(source, SizedModel())
    validate_refinement(source, compact)


@pytest.mark.asyncio
async def test_late_impossible_physical_single_prevents_all_generation():
    source, _, _ = await fixture(['operation completed after delay'] * 7)
    last = records_for_chunk(source.chunks[-1])[-1]['timestamp']
    model = SizedModel(limit=1, bad_key=last)
    with pytest.raises(SectionModelError, match='compact_token_singleton_exceeded'):
        await compact_summary_batch(source, model)
    assert model.calls == 0 and len(model.checked) == 13
    assert model.checked[-1] == (last,)
    assert sum(len(keys) == 1 for keys in model.checked) == 7
    assert all(chunk.compact_evidence is None for chunk in source.chunks)


@pytest.mark.asyncio
async def test_fitting_parent_requires_no_unused_singleton_proofs():
    source, views, options = await fixture(['operation completed after delay'] * 7)
    original = records_for_chunk(source.chunks[0])
    # The hypothetical singleton would fail, but it is never sent. This is a
    # nonmonotone synthetic oracle, not a tokenizer measurement or bound.
    model = SizedModel(limit=100, bad_key=original[-1]['timestamp'])
    packed = await _pack_compact_physical(source, model, SummaryConfig(), views, **options)
    assert packed == source
    assert model.checked == [tuple(row['timestamp'] for row in original)]
    assert model.calls == 0
    compact = await compact_summary_batch(source, SizedModel(limit=100, bad_key=original[-1]['timestamp']))
    validate_refinement(source, compact)


@pytest.mark.asyncio
async def test_admission_cache_is_local_to_one_plan():
    source, views, options = await fixture(['operation completed after delay'] * 7)
    model = SizedModel(limit=100)
    await _pack_compact_physical(source, model, SummaryConfig(), views, **options)
    assert len(model.checked) == 1
    model.limit = 1
    model.bad_key = records_for_chunk(source.chunks[-1])[-1]['timestamp']
    with pytest.raises(SectionModelError, match='compact_token_singleton_exceeded'):
        await _pack_compact_physical(source, model, SummaryConfig(), views, **options)
    assert len(model.checked) == 14
    assert model.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'invalid', 'exception', 'timeout', 'cancel'])
async def test_preflight_failures_and_cancellation_never_generate(failure):
    source, _, _ = await fixture(['operation completed after delay'] * 2)
    model = SizedModel()
    async def fail(*args):
        if failure == 'invalid':
            return 1
        if failure == 'timeout':
            await asyncio.Event().wait()
        if failure == 'cancel':
            raise asyncio.CancelledError()
        raise RuntimeError('PRIVATE_SOURCE')
    model.preflight_generation = None if failure == 'missing' else fail
    error = asyncio.CancelledError if failure == 'cancel' else SectionModelError
    with pytest.raises(error) as caught:
        await compact_summary_batch(source, model, config=SummaryConfig(timeout_seconds=.01))
    assert 'PRIVATE' not in str(caught.value)
    assert model.calls == 0


@pytest.mark.asyncio
async def test_counter_identity_is_pinned_across_plan_calls():
    counter = Counter()
    model = LocalModels(chat_model='mistral:7b', generation_token_counter=counter)
    assert await model.preflight_generation('instruction', {}, {})
    counter.identity = replace(IDENTITY, tokenizer_digest='d' * 64)
    with pytest.raises(ModelUnavailable, match='generation_token_proof_unavailable'):
        await model.preflight_generation('instruction', {}, {})
    assert len(counter.requests) == 1
