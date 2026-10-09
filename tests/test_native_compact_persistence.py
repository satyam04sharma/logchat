"""Real SQLite/WAL privacy boundaries; all generation and embedding calls mocked."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
import pytest_asyncio

from logchat.local.files import FileCapture
from logchat.local.rag_runtime import configure
from logchat.local.store import LocalStore
from logchat.rag.builder import prepare_preserved_batch, embed_batch
from logchat.rag.contracts import BuildResult, EmbeddedChunk, EmbeddingSpec, TimeWindow
from logchat.rag.embeddings import OllamaEmbeddingProvider
from logchat.rag.sections import COMPACT_VERSION, validate_compact_batch
from pipeline.models import ModelUnavailable
from pipeline.types import LogEvent

RAW = "UNSELECTED_ORIGINAL_BODY_9ccf"
FINGERPRINT = "ORIGINAL_PROVIDER_FINGERPRINT_53ae"


class Embeddings:
    spec = EmbeddingSpec("ollama", "mock", "compact-test-digest", 8)
    preserve_content = True
    local_only = True

    async def embed(self, texts, **kwargs):
        assert all(RAW not in text and FINGERPRINT not in text for text in texts)
        return [(1., .1, .2, .3, .4, .5, .6, .7) for _ in texts]


class Generation:
    local_only = True
    preserve_content = True
    chat_model = "mock-compact"

    def __init__(self):
        self.calls = []
        self.fail = False
        self.started = None
        self.release = None

    async def generate(self, instruction, context, schema):
        self.calls.append(context)
        if self.started is not None:
            self.started.set()
            await self.release.wait()
        if self.fail:
            raise KeyError(RAW + " provider failure")
        if "items" in context:
            return {"sections": [[item["ref"] for item in context["items"]]]}
        assert "records" in context
        return {"summary": "The service reported a request failure.",
                "important_fields": [field["field_ref"] for field in context["field_catalog"]
                                     if field["key"] == "error_code"], "uncertainties": []}


@pytest_asyncio.fixture
async def configured(tmp_path, monkeypatch):
    async def create(**kwargs):
        return Embeddings()
    model = Generation()
    monkeypatch.setattr(OllamaEmbeddingProvider, "create", create)
    monkeypatch.setattr("pipeline.models.LocalModels", lambda **kwargs: model)
    await configure(tmp_path / "state", model="mock", dimensions=8,
                    chat_model="mock-compact", content_policy="local_model_compact")
    store = LocalStore(tmp_path / "state")
    project = store.create_project("compact test")
    source = store.create_source(project["id"], "stdout", "dev", "push", None)
    return store, project, source, model


def event(source, *, identifier="event-one"):
    return LogEvent(identifier, datetime.now(timezone.utc), source["id"], "app", "error",
                    "Request failed error_code=LIB042 discarded detail " + RAW, FINGERPRINT, duration_ms=17, request_status=503)


def assert_no_originals(store):
    runtime = store.rag_runtime
    with runtime.backend.connect() as connection:
        values = [row[0] for row in connection.execute("SELECT payload FROM rag_schedule_jobs")]
        values += [row[0] for row in connection.execute("SELECT payload FROM rag_chunks")]
        values += [row[0] for row in connection.execute("SELECT summary FROM rag_chunks_fts")]
        logical = json.dumps(values)
    for token in (RAW, FINGERPRINT, "LOGCHAT_LOCAL_RECORDS_V1"):
        assert token not in logical
        for path in store.state_dir.iterdir():
            if path.is_file():
                assert token.encode() not in path.read_bytes(), path.name


@pytest.mark.asyncio
async def test_only_compact_payloads_survive_queue_failure_restart_and_publication(configured, monkeypatch):
    store, project, source, model = configured
    runtime = store.rag_runtime
    original = event(source)
    # Keep a read snapshot alive so WAL contents are inspected, not only final pages.
    pinned = runtime.backend.connect()
    pinned.execute("BEGIN")
    pinned.execute("SELECT count(*) FROM rag_index_meta").fetchone()
    try:
        receipt = await runtime.ingest_async(project["id"], source["id"], [original])
        assert receipt["original_events_stored"] is False
        assert len(model.calls) == 2
        assert RAW in json.dumps(model.calls)  # Originals reached only the local model mock.
        with runtime.backend.connect() as connection:
            payload = json.loads(connection.execute("SELECT payload FROM rag_schedule_jobs").fetchone()[0])
        assert payload["chunks"][0]["compression_version"] == COMPACT_VERSION
        assert "supporting_records" not in payload["chunks"][0]
        compact = json.loads(payload["chunks"][0]["compact_evidence"])
        assert "message" not in compact["records"][0]
        assert compact["important_fields"] == [{"key": "error_code", "value": "LIB042", "event_refs": ["e0"]}]
        assert_no_originals(store)
        runtime.scheduler.dispatch(now=datetime.now(timezone.utc) + timedelta(seconds=10))
        job = runtime.scheduler.claim_jobs()[0]
        validate_compact_batch(job.batch)
        assert runtime.scheduler.fail(job, "embedding_unavailable", retryable=False)
        assert_no_originals(store)
        restarted = LocalStore(store.state_dir).rag_runtime
        assert restarted.scheduler.retry_failed(job.batch.identity) == 1
        # The worker must embed committed compact work without repeating generation.
        monkeypatch.setattr(restarted.model_profile, "generation", lambda role: (_ for _ in ()).throw(AssertionError("no generation during embedding")))
        await restarted.start()
        try:
            async with asyncio.timeout(10):
                while restarted.backend.statistics()["vectors"] != 1:
                    await asyncio.sleep(.01)
        finally:
            await restarted.stop()
        assert_no_originals(store)
        with runtime.backend.connect() as connection:
            assert connection.execute("SELECT payload,state FROM rag_schedule_jobs").fetchone()[:] == ("", "completed")
            assert connection.execute("SELECT count(*) FROM rag_schedule_event_digests").fetchone()[0] == 1
            assert "LIB042" in connection.execute("SELECT summary FROM rag_chunks_fts").fetchone()[0]
        assert len(model.calls) == 2
    finally:
        pinned.close()


@pytest.mark.asyncio
async def test_summary_failure_keeps_file_cursor_and_watcher_recovers(configured, tmp_path):
    store, project, source, model = configured
    path = tmp_path / "application.log"
    path.write_text("Request failed discarded=" + RAW + "\n")
    capture = FileCapture(store)
    capture.configure(project["id"], source["id"], path, from_start=True)
    model.fail = True
    await asyncio.to_thread(capture.poll)
    row = capture.status(project["id"])["sources"][0]
    assert row["offset"] == 0 and row["accepted"] == 0 and row["state"] == "unavailable"
    assert (row["error_category"], row["error_phase"]) == ("section_provider_unavailable", "partition")
    assert store.rag_runtime.scheduler.status(store.rag_runtime.identity(project["id"], source["id"]))["jobs"] == {}
    assert_no_originals(store)
    model.fail = False
    await capture.start()
    try:
        async with asyncio.timeout(5):
            while capture.status(project["id"])["sources"][0]["offset"] == 0:
                await asyncio.sleep(.01)
    finally:
        await capture.stop()
    row = capture.status(project["id"])["sources"][0]
    assert row["offset"] == path.stat().st_size and row["accepted"] == 1
    assert row["state"] == "watching"
    assert row["error_category"] is None and row["error_phase"] is None
    assert_no_originals(store)


@pytest.mark.asyncio
async def test_sync_bridge_inside_running_loop_replay_digest_and_changed_id_rejection(configured):
    store, project, source, model = configured
    original = event(source)
    receipt = store.rag_runtime.ingest(project["id"], source["id"], [original])
    assert receipt["jobs"]
    calls = len(model.calls)
    replay = await store.rag_runtime.ingest_async(project["id"], source["id"], [replace(original, ts=original.ts + timedelta(seconds=5))])
    assert replay["deduplicated"] == 1 and not replay["jobs"]
    assert len(model.calls) == calls
    with pytest.raises(ModelUnavailable, match="event_identity_content_conflict"):
        await store.rag_runtime.ingest_async(project["id"], source["id"], [replace(original, message="Changed content")])
    assert len(model.calls) == calls
    assert_no_originals(store)


@pytest.mark.asyncio
async def test_raw_queue_and_backend_write_rejected_before_any_record_persistence(configured):
    store, project, source, _ = configured
    runtime = store.rag_runtime
    original = event(source)
    identity = runtime.identity(project["id"], source["id"])
    raw = prepare_preserved_batch(identity, [original], TimeWindow(original.ts, original.ts + timedelta(seconds=1)))
    runtime.scheduler.register_source(identity)
    with pytest.raises(ValueError, match="persistence|compact"):
        runtime.scheduler.enqueue(raw)
    built = BuildResult(raw.batch_id, (EmbeddedChunk(raw.chunks[0], (1., .1, .2, .3, .4, .5, .6, .7), runtime.spec),), raw.coverage)
    connection = runtime.backend.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="persistence|compact"):
            runtime.backend.write_result(connection, built)
        connection.commit()
    finally:
        connection.close()
    assert runtime.scheduler.status(identity)["jobs"] == {}
    assert runtime.backend.statistics()["chunks"] == 0
    assert_no_originals(store)


@pytest.mark.asyncio
async def test_async_intake_has_bounded_concurrency_and_releases_slot_after_failure(configured):
    store, project, source, model = configured
    model.started, model.release = asyncio.Event(), asyncio.Event()
    original = event(source)
    task = asyncio.create_task(store.rag_runtime.ingest_async(project["id"], source["id"], [original]))
    await model.started.wait()
    with pytest.raises(ModelUnavailable, match="intake_busy"):
        await store.rag_runtime.ingest_async(project["id"], source["id"], [event(source, identifier="second")])
    model.fail = True
    model.release.set()
    with pytest.raises(ModelUnavailable, match="compact_intake_unavailable"):
        await task
    model.fail = False
    model.started = None
    result = await store.rag_runtime.ingest_async(project["id"], source["id"], [original])
    assert result["jobs"]
    assert_no_originals(store)


@pytest.mark.asyncio
async def test_file_watcher_retries_top_level_poll_failure_without_crashing(configured, monkeypatch):
    store, _, _, _ = configured
    capture = FileCapture(store)
    recovered = asyncio.Event()
    loop = asyncio.get_running_loop()
    count = 0
    def poll():
        nonlocal count
        count += 1
        if count == 1:
            raise KeyError(RAW)
        loop.call_soon_threadsafe(recovered.set)
    monkeypatch.setattr(capture, "poll", poll)
    await capture.start()
    try:
        async with asyncio.timeout(3):
            await recovered.wait()
        assert capture.task is not None and not capture.task.done()
    finally:
        await capture.stop()
