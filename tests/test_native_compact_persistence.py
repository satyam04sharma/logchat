"""Real SQLite/WAL privacy boundaries; all generation and embedding calls mocked."""
from tests.physical_preflight import SyntheticPhysicalPreflight
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


class Generation(SyntheticPhysicalPreflight):
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
        from tests.catalog_selector import select_topics
        result = select_topics(context, ['Request failed'])
        result['important_fields'] = [field['field_ref'] for field in context['field_catalog']
                                     if field['key'] == 'error_code']
        return result



@pytest_asyncio.fixture
async def configured(tmp_path, monkeypatch):
    from tests.synthetic_embedding_admission import install
    install(monkeypatch)
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


def intake_diagnostics(caplog):
    return [json.loads(record.getMessage().removeprefix("native_intake "))
            for record in caplog.records if record.getMessage().startswith("native_intake ")]


@pytest.mark.asyncio
async def test_runtime_passes_trusted_views_to_grouping_and_compact_with_canonical_catalog(configured):
    from connectors.local import parse_push_events
    store, project, source, model = configured
    events = parse_push_events([
        {"event_id": f"projection-{index}", "message": "Request failed " + RAW,
         "timestamp": "2026-10-10T00:00:00Z", "duration_ms": index + .5,
         "request_status": 503, "error_code": "LIB042"}
        for index in range(12)], source=source["id"], preserve_fields=True)
    receipt = await store.rag_runtime.ingest_async(project["id"], source["id"], events)
    grouping = [call for call in model.calls if "items" in call]
    assert len(grouping) == 1 and len(grouping[0]["items"]) == 1
    message = grouping[0]["items"][0]["messages"][0]
    assert RAW in message and "LIB042" in message and "projection-" not in message
    compact = [call for call in model.calls if "records" in call]
    assert len(compact) == 1 and len(compact[0]["records"]) == 12
    assert {row["message"] for row in compact[0]["records"]} == {message}
    assert {field["value"] for field in compact[0]["field_catalog"] if field["key"] == "event_id"} == {
        event.event_id for event in events}
    assert compact[0]["exact_metrics"]["event_count"] == 12
    assert compact[0]["exact_metrics"]["duration_sum_ms"] == 72
    assert receipt["accepted"] == 12 and receipt["jobs"]
    calls = len(model.calls)
    replay = await store.rag_runtime.ingest_async(project["id"], source["id"], events)
    assert replay["deduplicated"] == 12 and len(model.calls) == calls
    assert_no_originals(store)


@pytest.mark.asyncio
async def test_intake_diagnostics_count_calls_and_preserve_model_requests(configured, caplog):
    caplog.set_level("INFO", logger="logchat.local.rag_runtime")
    store, project, source, model = configured
    original = event(source)
    result = await store.rag_runtime.ingest_async(project["id"], source["id"], [original])
    records = intake_diagnostics(caplog)
    finished = records[-1]
    assert finished["kind"] == "finished" and finished["status"] == "ok"
    assert finished["calls"] == {"chunking": 1, "summary": 1}
    assert len(model.calls) == sum(finished["calls"].values())
    assert {record["stage"] for record in records} == {
        "admission", "prepare", "grouping", "compact", "validate_enqueue"}
    assert len({record["intake"] for record in records}) == 1
    assert result["jobs"] and result["accepted"] == 1
    logged = caplog.text
    for private in (RAW, FINGERPRINT, original.event_id, source["id"], project["id"], model.chat_model):
        assert private not in logged
    assert_no_originals(store)
    caplog.clear()
    replay = await store.rag_runtime.ingest_async(project["id"], source["id"], [original])
    assert replay["deduplicated"] == 1
    assert intake_diagnostics(caplog)[-1]["calls"] == {"chunking": 0, "summary": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "cancel", "deadline"])
@pytest.mark.parametrize("stage", ["grouping", "compact"])
async def test_intake_diagnostics_identify_unfinished_stage_without_error_content(
        configured, caplog, monkeypatch, failure, stage):
    from logchat.local import rag_runtime
    caplog.set_level("INFO", logger="logchat.local.rag_runtime")
    store, project, source, model = configured
    if failure == "provider":
        model.fail = True
    else:
        model.started, model.release = asyncio.Event(), asyncio.Event()
    if stage == "compact":
        generate = model.generate

        async def fail_only_compact(instruction, context, schema):
            if "items" in context:
                model.calls.append(context)
                return {"sections": [[item["ref"] for item in context["items"]]]}
            return await generate(instruction, context, schema)

        monkeypatch.setattr(model, "generate", fail_only_compact)
    if failure == "deadline":
        timeout = asyncio.timeout
        monkeypatch.setattr(rag_runtime.asyncio, "timeout", lambda seconds: timeout(.05 if seconds == 300 else seconds))
    task = asyncio.create_task(store.rag_runtime.ingest_async(project["id"], source["id"], [event(source)]))
    if failure == "cancel":
        await model.started.wait()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ModelUnavailable):
        await task
    records = intake_diagnostics(caplog)
    finished = records[-1]
    assert finished["stage"] == stage
    assert finished["status"] == ("cancelled" if failure == "cancel" else "failed")
    assert finished["calls"] == {"chunking": 1, "summary": int(stage == "compact")}
    assert records[-2]["kind"] == "model_finished"
    assert records[-2]["status"] == ("failed" if failure == "provider" else "cancelled")
    assert finished["stage_seconds"] >= 0 and finished["seconds"] >= finished["stage_seconds"]
    assert RAW not in caplog.text and FINGERPRINT not in caplog.text
    assert_no_originals(store)
    # Failure and cancellation still release the shared intake/settings slot.
    model.fail, model.started = False, None
    assert (await store.rag_runtime.ingest_async(project["id"], source["id"], [event(source)]))["jobs"]


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
async def test_summary_only_sources_take_reserved_retry_turns_across_entrypoints(configured, monkeypatch):
    from logchat.local import rag_runtime
    from logchat.rag.scheduler import _identity_key
    monkeypatch.setattr(rag_runtime, "_INTAKE_SLOT", rag_runtime._FairIntakeSlot())
    store, project, hot, model = configured
    cold = store.create_source(project["id"], "cold", "dev", "push", None)
    third = store.create_source(project["id"], "third", "dev", "push", None)
    model.started, model.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(store.rag_runtime.ingest_async(project["id"], hot["id"], [event(hot)]))
    await model.started.wait()
    for source in (cold, third, cold):
        with pytest.raises(ModelUnavailable, match="intake_busy"):
            await store.rag_runtime.ingest_async(project["id"], source["id"], [event(source)])
    model.release.set()
    await task
    model.started = None
    with pytest.raises(ModelUnavailable, match="intake_busy"):
        store.rag_runtime.ingest(project["id"], hot["id"], [event(hot, identifier="hot-second")])
    # A synchronous retry consumes the same reservation as async ingestion.
    cold_receipt = await asyncio.to_thread(store.rag_runtime.ingest, project["id"], cold["id"], [event(cold)])
    assert cold_receipt["accepted"] == 1
    with pytest.raises(ModelUnavailable, match="intake_busy"):
        await store.rag_runtime.ingest_async(project["id"], hot["id"], [event(hot, identifier="hot-second")])
    assert (await store.rag_runtime.ingest_async(project["id"], third["id"], [event(third)]))["accepted"] == 1
    assert (await store.rag_runtime.ingest_async(project["id"], hot["id"], [event(hot, identifier="hot-second")]))["accepted"] == 1
    for source, expected in ((hot, 2), (cold, 1), (third, 1)):
        identity = store.rag_runtime.identity(project["id"], source["id"])
        with store.rag_runtime.backend.connect() as connection:
            assert connection.execute("SELECT count(*) FROM rag_schedule_event_digests WHERE scope=?", (_identity_key(identity),)).fetchone()[0] == expected
    assert_no_originals(store)


@pytest.mark.asyncio
async def test_cancelled_intake_releases_slot_to_waiting_source(configured, monkeypatch):
    from logchat.local import rag_runtime
    monkeypatch.setattr(rag_runtime, "_INTAKE_SLOT", rag_runtime._FairIntakeSlot())
    store, project, hot, model = configured
    cold = store.create_source(project["id"], "cold", "dev", "push", None)
    model.started, model.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(store.rag_runtime.ingest_async(project["id"], hot["id"], [event(hot)]))
    await model.started.wait()
    with pytest.raises(ModelUnavailable, match="intake_busy"):
        await store.rag_runtime.ingest_async(project["id"], cold["id"], [event(cold)])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    model.started = None
    with pytest.raises(ModelUnavailable, match="intake_busy"):
        await store.rag_runtime.ingest_async(project["id"], hot["id"], [event(hot)])
    assert (await store.rag_runtime.ingest_async(project["id"], cold["id"], [event(cold)]))["accepted"] == 1
    assert (await store.rag_runtime.ingest_async(project["id"], hot["id"], [event(hot)]))["accepted"] == 1
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


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_mode", ["summary_only", "retain_until_summarized"])
@pytest.mark.parametrize("explicit_fingerprint", [False, True])
async def test_canonical_parser_upgrade_replay_and_restart(configured, capture_mode, explicit_fingerprint):
    """Frozen pre-optimization canonical evidence must survive the parser upgrade."""
    from connectors.local import parse_event
    from logchat.local.raw_capture import content_digest, save_capture_policy

    store, project, source, model = configured
    body = {"event_id": "canonical-1542801", "message": "Request failed request 777",
            "timestamp": "2026-10-10T12:00:00Z", "error_code": "RARE-042",
            "duration_ms": 17, "request_status": 503, "release": "r1"}
    # Recorded from the parser preceding 8c094f1, independently of candidate normalization.
    canonical_message = ('Request failed request 777\nStructured fields: '
                         '{"error_code": "RARE-042", "event_id": "canonical-1542801"}')
    inferred = "d386bd79213873270861d5ea9940350fa6a1872b5807a58547480cb88dbb7093"
    if explicit_fingerprint:
        body["fingerprint"] = "producer-fingerprint"
    legacy = LogEvent(body["event_id"], datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
                      "fixture-source", "application", "info", canonical_message,
                      body.get("fingerprint", inferred), release="r1", duration_ms=17.0, request_status=503)
    if not explicit_fingerprint:
        assert content_digest(legacy) == "765a7354dd8a64255dbdf821ac1831ac5f10fe17fd07cdf3b771e7f2102c2750"
    legacy = replace(legacy, source=source["id"])
    save_capture_policy(store.state_dir, mode=capture_mode)
    receipt = await store.rag_runtime.ingest_async(project["id"], source["id"], [legacy])
    assert receipt["accepted"] == 1 and receipt["deduplicated"] == 0
    calls = len(model.calls)
    for runtime in (store.rag_runtime, LocalStore(store.state_dir).rag_runtime):
        candidate = parse_event(body, source=source["id"], preserve_fields=True)
        assert candidate == legacy
        # Exercise the actual HTTP parse boundary with the unchanged old body.
        import httpx
        from logchat.local.app import create_app
        app = create_app(store.state_dir, 18949, capture_host=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://127.0.0.1:18949") as client:
            response = await client.post(f"/projects/{project['id']}/events",
                json={"source_id": source["id"], "events": [body]},
                headers={"Authorization": "Bearer " + source["token"]})
            assert response.status_code == 200
            assert response.json()["deduplicated"] == 1
            changed_response = await client.post(f"/projects/{project['id']}/events",
                json={"source_id": source["id"], "events": [{**body, "message": "changed"}]},
                headers={"Authorization": "Bearer " + source["token"]})
            assert changed_response.status_code == 503
            assert changed_response.headers["X-Logchat-Processing-Category"] == "event_identity_content_conflict"
        replay = await runtime.ingest_async(project["id"], source["id"], [candidate])
        assert replay["deduplicated"] == 1
        assert not replay.get("jobs")
        # Every changed replay must fail before generation or durable replacement.
        for change in ({"message": "Changed request 778"}, {"error_code": "OTHER-043"},
                       {"request_status": 504}, {"duration_ms": 18}, {"release": "r2"},
                       {"fingerprint": "changed-fingerprint"}):
            changed = parse_event({**body, **change}, source=source["id"], preserve_fields=True)
            with pytest.raises(ModelUnavailable, match="event_identity_content_conflict"):
                await runtime.ingest_async(project["id"], source["id"], [changed])
        with pytest.raises(ModelUnavailable, match="event_identity_content_conflict"):
            await runtime.ingest_async(project["id"], source["id"], [replace(candidate, source="changed-source")])
        assert len(model.calls) == calls
    if capture_mode == "summary_only":
        for path in store.state_dir.iterdir():
            if path.is_file():
                assert canonical_message.encode() not in path.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['deadline', 'cancel'])
async def test_physical_preflight_uses_existing_intake_deadline_and_releases_slot(configured, monkeypatch, failure):
    from logchat.local import rag_runtime
    store, project, source, model = configured
    entered, cancelled = asyncio.Event(), asyncio.Event()
    original_preflight = model.preflight_generation
    original_timeout = asyncio.timeout
    seen_deadlines = []

    def timeout(seconds):
        seen_deadlines.append(seconds)
        return original_timeout(.05 if seconds == 300 else seconds)

    async def waiting(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(model, 'preflight_generation', waiting)
    if failure == 'deadline':
        monkeypatch.setattr(rag_runtime.asyncio, 'timeout', timeout)
    original = event(source)
    task = asyncio.create_task(store.rag_runtime.ingest_async(project['id'], source['id'], [original]))
    await entered.wait()
    if failure == 'cancel':
        task.cancel()
    with pytest.raises(asyncio.CancelledError if failure == 'cancel' else ModelUnavailable):
        await task
    assert cancelled.is_set()
    assert all('items' in call for call in model.calls)
    if failure == 'deadline':
        assert seen_deadlines == [300]
    with store.rag_runtime.backend.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM rag_schedule_jobs').fetchone()[0] == 0
    monkeypatch.setattr(model, 'preflight_generation', original_preflight)
    monkeypatch.setattr(rag_runtime.asyncio, 'timeout', original_timeout)
    receipt = await store.rag_runtime.ingest_async(project['id'], source['id'], [original])
    assert receipt['accepted'] == 1 and receipt['jobs']
