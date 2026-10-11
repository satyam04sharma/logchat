"""Strict transport recovery with real leases/SQLite and synthetic vectors only."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from logchat.rag.scheduler import SQLiteSchedulerStore, _encode
from logchat.rag.sections import (
    COMPACT_EVIDENCE_V2, _compact_id, _hash, _json,
    upgrade_embedding_projection, validate_projection_upgrade,
)
from tests.test_compact_embedding_versions import fixture_chunk
from tests.test_rag_scheduler import result, writer
from tests.test_rag_sections import NOW, batch, event


def prepared():
    return replace(batch([event(), event(1)]), chunks=(fixture_chunk(), fixture_chunk(1)))


def ready(store, original):
    store.register_source(original.identity, now=NOW)
    store.enqueue(original, now=NOW, event_digests={key: _hash(key) for key in original.event_keys})
    store.dispatch(now=NOW + timedelta(seconds=30))
    return store.claim_jobs(now=NOW + timedelta(seconds=30))[0]


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return (conn.execute('SELECT payload_hash FROM rag_schedule_jobs').fetchall(),
                conn.execute('SELECT * FROM rag_schedule_event_keys ORDER BY event_key').fetchall(),
                conn.execute('SELECT * FROM rag_schedule_event_digests ORDER BY event_key').fetchall())


def test_strict_recovery_requires_explicit_retry_and_preserves_replay_restart(tmp_path):
    path = tmp_path / 'memory.sqlite'
    store = SQLiteSchedulerStore(path)
    original = prepared()
    job = ready(store, original)
    initial = snapshot(path)
    upgraded = upgrade_embedding_projection(original)
    validate_projection_upgrade(original, upgraded)
    with pytest.raises(ValueError, match='requires_explicit_failed_retry'):
        store.refine(job, upgraded, now=NOW + timedelta(seconds=31))
    assert store.recover_projection(job, now=NOW + timedelta(seconds=31)) == job
    assert store.retry_failed(original.identity, now=NOW + timedelta(seconds=31)) == 0
    assert store.fail(job, 'embedding_unavailable', retryable=False, now=NOW + timedelta(seconds=31))
    assert store.recover_projection(job, now=NOW + timedelta(seconds=32)) is None
    assert store.retry_failed(original.identity, now=NOW + timedelta(seconds=32), limit=1) == 1
    lease = store.claim_jobs(now=NOW + timedelta(seconds=32), lease_seconds=1)[0]
    recovered = store.recover_projection(lease, now=NOW + timedelta(seconds=32))
    assert recovered.batch == upgraded
    assert snapshot(path) == initial
    # Old in-memory payload cannot complete or refine after durable replacement.
    with pytest.raises(ValueError, match='exact prepared'):
        store.complete(lease, result(original), writer, now=NOW + timedelta(seconds=32))
    with pytest.raises(ValueError, match='payload_changed'):
        store.refine(lease, upgraded, now=NOW + timedelta(seconds=32))
    restarted = SQLiteSchedulerStore(path)
    fresh = restarted.claim_jobs(now=NOW + timedelta(seconds=33))[0]
    assert fresh.batch == upgraded
    assert store.recover_projection(recovered, now=NOW + timedelta(seconds=33)) is None
    assert restarted.recover_projection(fresh, now=NOW + timedelta(seconds=33)).batch == upgraded
    assert restarted.enqueue(original, now=NOW) == fresh.job_id
    assert restarted.complete(fresh, result(upgraded), writer, now=NOW + timedelta(seconds=34))
    assert restarted.refine(fresh, upgraded, now=NOW + timedelta(seconds=34)) is None
    assert restarted.retry_failed(original.identity, now=NOW + timedelta(seconds=34)) == 0
    assert snapshot(path) == initial
    assert restarted.enqueue(original, now=NOW) == fresh.job_id


@pytest.mark.parametrize('mutation', ['boolean', 'float', 'nested', 'prose', 'model', 'notes', 'metric',
                                    'coverage', 'catalog', 'refs', 'uncertainty', 'pattern', 'id', 'order', 'count'])
def test_projection_upgrade_rejects_every_other_change(mutation):
    original = prepared()
    upgraded = upgrade_embedding_projection(original)
    chunk = upgraded.chunks[0]
    evidence = json.loads(chunk.compact_evidence)
    fields = {field['key']: field for field in evidence['important_fields']}
    if mutation == 'boolean': fields['integer']['value'] = True
    elif mutation == 'float': fields['integer']['value'] = 1.0
    elif mutation == 'nested': fields['object']['value']['n'] = 2
    elif mutation == 'catalog': fields['id']['key'] = 'different_alias'
    elif mutation == 'refs': fields['id']['event_refs'] = ['e99']
    elif mutation == 'uncertainty': evidence['uncertainties'].append('Different uncertainty.')
    chunk = replace(chunk, compact_evidence=_json(evidence))
    if mutation == 'prose': chunk = replace(chunk, summary='Changed summary.')
    elif mutation == 'model': chunk = replace(chunk, summary_model='different-model')
    elif mutation == 'notes': chunk = replace(chunk, loss_notes=('different-note',))
    elif mutation == 'metric': chunk = replace(chunk, metrics=replace(chunk.metrics, duration_sum_ms=0.6))
    chunk = replace(chunk, pattern_id=_hash([chunk.compression_version, chunk.summary, evidence['important_fields']]),
                    chunk_id=_compact_id(chunk, chunk.compact_evidence, chunk.summary, chunk.summary_model))
    if mutation == 'pattern': chunk = replace(chunk, pattern_id='forged')
    elif mutation == 'id': chunk = replace(chunk, chunk_id='forged')
    upgraded = replace(upgraded, chunks=(chunk, upgraded.chunks[1]))
    if mutation == 'coverage':
        coverage = upgraded.coverage[0]
        upgraded = replace(upgraded, coverage=(replace(coverage, metrics=replace(coverage.metrics, duration_sum_ms=2)),))
    elif mutation == 'order': upgraded = replace(upgraded, chunks=tuple(reversed(upgraded.chunks)))
    elif mutation == 'count': upgraded = replace(upgraded, chunks=upgraded.chunks[:1])
    with pytest.raises(ValueError): validate_projection_upgrade(original, upgraded)


def test_mixed_upgrade_is_idempotent_and_downgrades_reject():
    original = prepared()
    v2 = upgrade_embedding_projection(original)
    mixed = replace(original, chunks=(original.chunks[0], v2.chunks[1]))
    assert upgrade_embedding_projection(mixed) == v2
    assert upgrade_embedding_projection(v2) == v2
    with pytest.raises(ValueError): validate_projection_upgrade(v2, original)


@pytest.mark.asyncio
async def test_normal_worker_uses_returned_upgraded_payload_without_generation(tmp_path, monkeypatch):
    from logchat.local.rag_runtime import configure
    from logchat.local.store import LocalStore
    from logchat.rag.contracts import EmbeddingSpec
    from logchat.rag.embeddings import OllamaEmbeddingProvider
    calls = []
    class Provider:
        local_only = preserve_content = True
        spec = EmbeddingSpec('ollama', 'mock', 'digest:prefix-v1', 8)
        async def embed(self, texts, **kwargs):
            calls.extend(texts)
            return [[1.] + [0.] * 7 for _ in texts]
    async def create(**kwargs): return Provider()
    async def forbidden(*args, **kwargs): raise AssertionError('generation forbidden')
    monkeypatch.setattr(OllamaEmbeddingProvider, 'create', create)
    monkeypatch.setattr('pipeline.models.LocalModels.generate', forbidden)
    await configure(tmp_path, model='mock', dimensions=8, chat_model='mock', content_policy='local_model_compact')
    runtime = LocalStore(tmp_path).rag_runtime
    original = prepared()
    lease = ready(runtime.scheduler, original)
    runtime.scheduler.fail(lease, 'embedding_unavailable', retryable=False, now=NOW + timedelta(seconds=31))
    runtime.scheduler.retry_failed(original.identity, now=NOW + timedelta(seconds=32))
    calls.clear()
    await runtime.start()
    try:
        async with asyncio.timeout(10):
            while runtime.backend.statistics()['vectors'] != 2: await asyncio.sleep(.01)
    finally: await runtime.stop()
    assert len(calls) == 2
    assert all('event_refs' not in text for text in calls)
    assert runtime.scheduler.status(original.identity)['jobs'] == {'completed': 1}
    stored = runtime.backend.list_chunks(owner_id=original.identity.owner_id, project_id=original.identity.project_id)
    assert {c.chunk_id for c in stored} == {c.chunk_id for c in upgrade_embedding_projection(original).chunks}
    assert all(json.loads(c.compact_evidence)['version'] == COMPACT_EVIDENCE_V2 for c in stored)


def test_existing_schema_defaults_to_no_recovery_authorization(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    store = SQLiteSchedulerStore(path)
    original = prepared()
    job = ready(store, original)
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE rag_schedule_jobs DROP COLUMN projection_recovery')
    migrated = SQLiteSchedulerStore(path)
    assert migrated.recover_projection(job, now=NOW + timedelta(seconds=31)) == job
    with pytest.raises(ValueError, match='requires_explicit_failed_retry'):
        migrated.refine(job, upgrade_embedding_projection(original), now=NOW + timedelta(seconds=31))


def test_typed_forgery_and_rejected_refinement_leave_durable_payload_unchanged(tmp_path):
    path = tmp_path / 'memory.sqlite'
    store = SQLiteSchedulerStore(path)
    original = prepared()
    job = ready(store, original)
    store.fail(job, 'embedding_unavailable', retryable=False, now=NOW + timedelta(seconds=31))
    store.retry_failed(original.identity, now=NOW + timedelta(seconds=32))
    lease = store.claim_jobs(now=NOW + timedelta(seconds=32))[0]
    coverage = original.coverage[0]
    # Python dataclass equality accepts this int/float mutation; canonical JSON does not.
    altered = replace(original, coverage=(replace(coverage, metrics=replace(coverage.metrics, duration_sum_ms=2)),))
    assert altered == original and _encode(altered) != _encode(original)
    forged = replace(lease, batch=altered)
    with pytest.raises(ValueError, match='payload_changed'):
        store.refine(forged, upgrade_embedding_projection(original), now=NOW + timedelta(seconds=32))
    with pytest.raises(ValueError, match='exact prepared'):
        store.complete(forged, result(original), writer, now=NOW + timedelta(seconds=32))
    upgraded = upgrade_embedding_projection(original)
    invalid = replace(upgraded, chunks=(replace(upgraded.chunks[0], loss_notes=('changed',)), upgraded.chunks[1]))
    with pytest.raises(ValueError, match='content_mismatch'):
        store.refine(lease, invalid, now=NOW + timedelta(seconds=32))
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT payload FROM rag_schedule_jobs').fetchone()[0] == _encode(original)
    assert store.recover_projection(lease, now=NOW + timedelta(seconds=32)).batch == upgraded
