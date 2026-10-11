"""Real fresh-intake/SQLite boundary with synthetic physical counts only."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from logchat.rag.embedding_subdivision import plan_embedding_subdivision
from pipeline.models import ModelUnavailable
from tests.test_native_compact_persistence import configured, event
from tests.test_embedding_subdivision_planner import setup


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'singleton', 'late_count', 'invalid_leaf', 'cancel', 'deadline', 'caps'])
async def test_entire_group_planned_closed_before_first_enqueue(configured, monkeypatch, failure):
    from logchat.rag import sections, embedding_subdivision
    store, project, source, _ = configured
    runtime = store.rag_runtime
    original_event = event(source)
    events = [replace(original_event, event_id=f'partition-{i}', ts=original_event.ts + timedelta(seconds=i))
              for i in range(4)]
    # Physical count is explicitly synthetic and independent of character size.
    def measure(text):
        n = json.loads(text.removeprefix('search_document: '))['whole_section_facts']['exact_metrics']['event_count']
        return 2049 if n > 2 or failure == 'singleton' else 2048
    provider, models, counter = setup(measure)
    generated, planned, enqueued, deadlines = [], [], [], []
    compact = sections.compact_summary_batch
    async def observe_compact(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        batch = await compact(*args, **kwargs)
        generated.append(batch)
        return batch
    monkeypatch.setattr(sections, 'compact_summary_batch', observe_compact)
    actual_count = counter.count
    async def count(request):
        proof = await actual_count(request)
        if failure == 'late_count' and len(counter.calls) == 3:
            raise ValueError('controlled_late_count')
        if failure == 'cancel':
            raise asyncio.CancelledError
        if failure == 'deadline':
            await asyncio.Event().wait()
        return proof
    counter.count = count
    if failure == 'invalid_leaf':
        subset = embedding_subdivision.record_subset
        def corrupt(parent, start, end):
            child = subset(parent, start, end)
            return replace(child, summary='invalid late child') if start else child
        monkeypatch.setattr(embedding_subdivision, 'record_subset', corrupt)
    if failure == 'caps':
        monkeypatch.setattr(embedding_subdivision, 'MAX_CHUNKS_PER_BATCH', 1)
    if failure == 'deadline':
        actual_timeout = asyncio.timeout
        monkeypatch.setattr(asyncio, 'timeout', lambda seconds: actual_timeout(.05 if seconds == 300 else seconds))
    async def planner(batch, *, deadline):
        assert batch is generated[-1] and deadlines == [deadline]
        result = await plan_embedding_subdivision(batch, provider, models, deadline=deadline)
        assert counter.closed
        planned.append(result)
        return result
    monkeypatch.setattr(runtime.model_profile, 'plan_embedding_batch', planner)
    enqueue = runtime.scheduler.enqueue
    def checked_enqueue(batch, **kwargs):
        assert counter.closed and batch is planned[-1].batch
        assert len(planned[-1].documents) == len(batch.chunks) == 2
        enqueued.append(batch)
        return enqueue(batch, **kwargs)
    monkeypatch.setattr(runtime.scheduler, 'enqueue', checked_enqueue)
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == 'cancel' else ModelUnavailable):
            await runtime.ingest_async(project['id'], source['id'], events)
        assert enqueued == []
    else:
        receipt = await runtime.ingest_async(project['id'], source['id'], events)
        assert len(receipt['jobs']) == len(enqueued) == 1
    with runtime.backend.connect() as connection:
        rows = connection.execute('SELECT payload_hash FROM rag_schedule_jobs').fetchall()
        assert [row[0] for row in rows] == [result.lineage.final_payload_sha256 for result in planned]
        assert connection.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0] == 0
    assert counter.closed and models.sessions == 1
