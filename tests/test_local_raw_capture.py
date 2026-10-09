"""Temporary originals use real SQLite; every generation/embedding provider is mocked."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from logchat.local.raw_capture import CaptureSpool, load_capture_policy, save_capture_policy
from logchat.local.rag_runtime import NativeRAGRuntime, configure
from logchat.local.store import LocalStore
from logchat.rag.contracts import EmbeddingSpec, SourceIdentity
from logchat.rag.embeddings import OllamaEmbeddingProvider
from logchat.rag.sections import COMPACT_VERSION
from pipeline.models import ModelUnavailable
from pipeline.types import LogEvent

RAW = "TEMPORARY_ORIGINAL_CANARY_54d271"
IDENTITY = SourceIdentity("local-os-user", "project-a", "env-a", "source-a")


def event(identifier="event-a", *, source="source-a", message=None):
    return LogEvent(identifier, datetime.now(timezone.utc), source, "web", "error",
                    message or "Request failed error_code=E42 detail=" + RAW, "fingerprint", request_status=503)


@pytest.fixture
def spool(tmp_path):
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    return CaptureSpool(tmp_path)


def test_default_and_bounded_settings_do_not_implicitly_enable(tmp_path):
    assert load_capture_policy(tmp_path)["mode"] == "summary_only"
    for kwargs in ({"mode": "auto"}, {"max_bytes": True}, {"max_bytes": 100},
                   {"retention_seconds": 59}, {"retention_seconds": 30 * 86400 + 1}):
        with pytest.raises(ValueError):
            save_capture_policy(tmp_path, **kwargs)
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    assert os.stat(tmp_path / "capture.json").st_mode & 0o777 == 0o600
    (tmp_path / "capture.json").write_text('{"mode":"retain_until_summarized"}')
    with pytest.raises(ValueError, match="invalid_capture_configuration"):
        load_capture_policy(tmp_path)


def test_existing_redacted_profile_cannot_enable_raw_capture(tmp_path):
    (tmp_path / "rag.json").write_text(json.dumps({"content_policy": "redacted_templates"}))
    with pytest.raises(ValueError, match="compact_model_profile_required"):
        save_capture_policy(tmp_path, mode="retain_until_summarized")
    assert load_capture_policy(tmp_path)["mode"] == "summary_only"


def test_atomic_replay_ids_are_stable_and_conflicts_do_not_partially_write(spool):
    original = event()
    receipt = spool.enqueue(IDENTITY, [original], now=1000)
    restarted = CaptureSpool(spool.state_dir)
    replay = restarted.enqueue(IDENTITY, [replace(original, ts=original.ts + timedelta(hours=1))], now=1001)
    assert receipt["batch_id"] == replay["batch_id"] and replay["deduplicated"] == 1
    with pytest.raises(ValueError, match="event_identity_content_conflict"):
        restarted.enqueue(IDENTITY, [event("new-event"), replace(original, message="changed")], now=1002)
    status = restarted.status(now=1002)
    assert status["pending_events"] == 1 and status["rejected_events"] == 2
    assert status["searchable"] is False
    assert os.stat(spool.path).st_mode & 0o777 == 0o600
    assert not (spool.state_dir / "rag.db").exists()


def test_invalid_batch_leaves_no_partial_raw_data(spool):
    with pytest.raises(ValueError, match="capture_invalid"):
        spool.enqueue(IDENTITY, [event(), replace(event("other"), request_status=999)])
    assert spool.status()["pending_events"] == 0
    assert RAW.encode() not in spool.path.read_bytes()


def test_capacity_rejects_whole_new_batch_and_expiry_counts_drops(tmp_path):
    save_capture_policy(tmp_path, mode="retain_until_summarized", max_bytes=1024 * 1024, retention_seconds=60)
    spool = CaptureSpool(tmp_path)
    payload = RAW + "x" * 11900
    spool.enqueue(IDENTITY, [event(str(i), message=payload) for i in range(10)], now=1000)
    with pytest.raises(ValueError, match="capture_capacity_exceeded"):
        spool.enqueue(IDENTITY, [event(str(i), message=payload) for i in range(10, 50)], now=1001)
    status = spool.status(now=1002)
    assert status["pending_events"] == 10 and status["rejected_events"] == 40
    assert status["disk_bytes"] <= 1024 * 1024
    assert spool.expire(now=1061) == 10
    status = spool.status(now=1061)
    assert status["pending_events"] == status["pending_bytes"] == 0
    assert status["expired_events"] == status["dropped_events"] == 10
    assert status["expired_bytes"] > 0
    assert RAW.encode() not in spool.path.read_bytes()
    assert not spool.path.with_name("raw-capture.db-wal").exists()


def test_leases_recover_and_only_owner_can_complete(spool):
    spool.enqueue(IDENTITY, [event()], now=1000)
    first = spool.claim(now=1001)
    assert first.events[0].message.endswith(RAW)
    assert spool.claim(now=1002) is None
    restarted = CaptureSpool(spool.state_dir)
    second = restarted.claim(now=1602)
    assert second.token != first.token
    assert restarted.complete(first) == 0
    assert restarted.complete(second) == 1
    assert RAW.encode() not in spool.path.read_bytes()
    assert restarted.enqueue(IDENTITY, [event()], now=1603)["deduplicated"] == 1
    assert restarted.status(now=1603)["pending_events"] == 0


def test_turning_capture_off_keeps_existing_pending_and_shorter_ttl_applies(spool):
    spool.enqueue(IDENTITY, [event()], now=1000)
    save_capture_policy(spool.state_dir, mode="summary_only", retention_seconds=60)
    assert spool.status(now=1010)["pending_events"] == 1
    with pytest.raises(ValueError, match="raw_capture_not_enabled"):
        spool.enqueue(IDENTITY, [event("new")], now=1010)
    assert spool.claim(now=1010) is not None
    assert spool.status(now=1061)["expired_events"] == 1


class Embeddings:
    spec = EmbeddingSpec("ollama", "mock-raw-capture", "mock-digest", 8)
    preserve_content = True
    local_only = True

    async def embed(self, texts, **kwargs):
        assert all(RAW not in text for text in texts)
        return [(1., .1, .2, .3, .4, .5, .6, .7) for _ in texts]


class Generation:
    local_only = True
    preserve_content = True
    chat_model = "mock-capture-model"

    def __init__(self):
        self.fail = False
        self.calls = 0

    async def generate(self, instruction, context, schema):
        self.calls += 1
        if self.fail:
            raise RuntimeError(RAW)
        if "items" in context:
            return {"sections": [[item["ref"] for item in context["items"]]]}
        return {"summary": "The service reported request errors.",
                "important_fields": [field["field_ref"] for field in context["field_catalog"] if field["key"] == "error_code"],
                "uncertainties": []}


@pytest.fixture
def raw_runtime(tmp_path):
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    store = LocalStore(tmp_path)
    project = store.create_project("raw capture")
    source = store.create_source(project["id"], "logs", "dev", "push", None)
    return store, project, source


async def configure_mock(store, monkeypatch):
    model = Generation()
    async def create(**kwargs):
        return Embeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider, "create", create)
    monkeypatch.setattr("pipeline.models.LocalModels", lambda **kwargs: model)
    await configure(store.state_dir, model="mock-raw-capture", dimensions=8,
                    chat_model="mock-capture-model", content_policy="local_model_compact")
    return model


async def require_compaction_started(runtime, task, started):
    """A one-shot drain may finish without calling generation; never wait forever."""
    start_task = asyncio.create_task(started.wait())
    try:
        done, _ = await asyncio.wait({task, start_task}, timeout=5,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task.cancelled():
            result = "cancelled"
        elif task.done() and task.exception() is not None:
            # Exceptions can contain original/provider text; report type only.
            result = f"raised:{type(task.exception()).__name__}"
        else:
            result = task.result() if task.done() else "pending"
        if start_task not in done:
            # Avoid pytest assertion rewriting: repr(done) includes the task's
            # original exception payload, bypassing the safe diagnostic above.
            raise AssertionError(
                f"compaction did not start: drain_done={task.done()}, "
                f"drain_result={result}, "
                f"runtime_error={runtime.error}, capture={runtime.raw_capture.status()}"
            )
    finally:
        start_task.cancel()
        await asyncio.gather(start_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_model_absence_is_pending_not_searchable_and_source_scope_is_enforced(raw_runtime):
    store, project, source = raw_runtime
    runtime = store.rag_runtime
    assert runtime.model_profile is runtime.backend is runtime.scheduler is None
    original = event(source=source["id"])
    receipt = await runtime.ingest_async(project["id"], source["id"], [original])
    assert receipt["original_events_stored"] and not receipt["indexed"]
    assert runtime.status(project["id"])["state"] == "awaiting_model"
    assert await runtime.drain_raw_once() == 0
    with pytest.raises(ModelUnavailable, match="model_not_configured"):
        await runtime.context(project["id"], None)
    with pytest.raises(ModelUnavailable, match="raw_capture_unavailable"):
        await runtime.ingest_async("foreign-project", source["id"], [event("foreign")])
    assert runtime.raw_capture.status(project["id"])["pending_events"] == 1
    assert runtime.raw_capture.status("foreign-project")["pending_events"] == 0
    save_capture_policy(store.state_dir, mode="summary_only")
    with pytest.raises(ModelUnavailable, match="model_not_configured"):
        await runtime.ingest_async(project["id"], source["id"], [original])
    assert not (store.state_dir / "rag.db").exists()


@pytest.mark.asyncio
async def test_same_runtime_recovers_after_profile_arrives_and_compact_failure_retains_raw(raw_runtime, monkeypatch):
    store, project, source = raw_runtime
    runtime = store.rag_runtime
    await runtime.ingest_async(project["id"], source["id"], [event(source=source["id"])])
    model = await configure_mock(store, monkeypatch)
    model.fail = True
    assert await runtime.drain_raw_once() == 0
    assert runtime.raw_capture.status()["pending_events"] == 1
    assert runtime.raw_capture.status()["failed_attempts"] == 1
    assert runtime.raw_capture.status()["pending_failure_categories"] == ["section_provider_unavailable"]
    assert runtime.status(project["id"])["processing_state"] == "raw_compaction_pending_retry"
    assert runtime.status(project["id"])["error_category"] == "section_provider_unavailable"
    assert RAW in runtime.raw_capture.path.read_bytes().decode("utf-8", errors="ignore")
    with runtime.backend.connect() as connection:
        assert connection.execute("SELECT count(*) FROM rag_schedule_jobs").fetchone()[0] == 0
    with runtime.raw_capture._connection() as connection:
        connection.execute("UPDATE raw_events SET available_at=0")
    save_capture_policy(store.state_dir, mode="summary_only")
    model.fail = False
    assert await runtime.drain_raw_once() == 1
    assert runtime.raw_capture.status()["pending_events"] == 0
    assert RAW.encode() not in runtime.raw_capture.path.read_bytes()
    with runtime.backend.connect() as connection:
        payload = connection.execute("SELECT payload FROM rag_schedule_jobs").fetchone()[0]
        assert json.loads(payload)["chunks"][0]["compression_version"] == COMPACT_VERSION
        assert RAW not in payload
    assert runtime.backend.statistics()["vectors"] == 0  # Compact enqueue, no indexing claim.
    for path in store.state_dir.glob("rag.db*"):
        assert RAW.encode() not in path.read_bytes()


@pytest.mark.asyncio
async def test_crash_after_compact_enqueue_retries_without_duplicate_counts(raw_runtime, monkeypatch):
    store, project, source = raw_runtime
    runtime = store.rag_runtime
    original = event(source=source["id"])
    await runtime.ingest_async(project["id"], source["id"], [original])
    model = await configure_mock(store, monkeypatch)
    complete = runtime.raw_capture.complete
    def crash(claim):
        raise RuntimeError("simulated process loss after compact commit")
    monkeypatch.setattr(runtime.raw_capture, "complete", crash)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        await runtime.drain_raw_once()
    assert runtime.raw_capture.status()["pending_events"] == 1
    generation_calls = model.calls
    monkeypatch.setattr(runtime.raw_capture, "complete", complete)
    with runtime.raw_capture._connection() as connection:
        connection.execute("UPDATE raw_events SET lease_until=0")
    assert await runtime.drain_raw_once() == 1
    assert model.calls == generation_calls
    with runtime.backend.connect() as connection:
        assert connection.execute("SELECT count(*) FROM rag_schedule_jobs").fetchone()[0] == 1
    with pytest.raises(ModelUnavailable, match="event_identity_content_conflict"):
        await runtime.ingest_async(project["id"], source["id"], [replace(original, message="changed same ID")])
    assert runtime.raw_capture.status()["pending_events"] == 0


@pytest.mark.asyncio
async def test_default_summary_only_model_failure_never_spools(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    model = await configure_mock(store, monkeypatch)
    runtime = NativeRAGRuntime(store)
    project = store.create_project("default")
    source = store.create_source(project["id"], "logs", "dev", "push", None)
    model.fail = True
    with pytest.raises(ModelUnavailable, match="compact_intake_unavailable"):
        await runtime.ingest_async(project["id"], source["id"], [event(source=source["id"])])
    assert runtime.raw_capture.status()["pending_events"] == 0
    for path in store.state_dir.iterdir():
        if path.is_file():
            assert RAW.encode() not in path.read_bytes()


@pytest.mark.asyncio
async def test_worker_automatically_drains_pending_then_embeds(raw_runtime, monkeypatch):
    store, project, source = raw_runtime
    runtime = store.rag_runtime
    await runtime.ingest_async(project["id"], source["id"], [event(source=source["id"])])
    await configure_mock(store, monkeypatch)
    await runtime.start()
    try:
        async with asyncio.timeout(12):
            while runtime.backend is None or runtime.backend.statistics()["vectors"] != 1:
                await asyncio.sleep(.02)
    finally:
        await asyncio.wait_for(runtime.stop(), timeout=5)
    assert runtime.raw_capture.status()["completed_events"] == 1
    assert RAW.encode() not in runtime.raw_capture.path.read_bytes()


@pytest.mark.asyncio
async def test_raw_admission_continues_while_compaction_is_waiting(raw_runtime, monkeypatch):
    store, project, source = raw_runtime
    runtime = store.rag_runtime
    await runtime.ingest_async(project["id"], source["id"], [event(source=source["id"])])
    model = await configure_mock(store, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    generate = model.generate
    async def paused(*args, **kwargs):
        started.set()
        await release.wait()
        return await generate(*args, **kwargs)
    monkeypatch.setattr(model, "generate", paused)
    task = asyncio.create_task(runtime.drain_raw_once())
    try:
        await require_compaction_started(runtime, task, started)
        result = await asyncio.wait_for(runtime.ingest_async(project["id"], source["id"],
            [event("during-compaction", source=source["id"])]), timeout=5)
        assert result["state"] == "raw_pending"
        assert not task.done() and not release.is_set()
        assert runtime.raw_capture.status()["pending_events"] == 2
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5)
    assert runtime.raw_capture.status()["pending_events"] == 1
    assert runtime.raw_capture.status()["completed_events"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["intake_busy", "already_leased", "retry_not_due"])
async def test_compaction_start_check_reports_completed_noop(raw_runtime, monkeypatch, cause):
    from logchat.local import rag_runtime

    store, project, source = raw_runtime
    runtime = store.rag_runtime
    await runtime.ingest_async(project["id"], source["id"], [event(source=source["id"])])
    model = await configure_mock(store, monkeypatch)
    claim = None
    slot_reserved = False
    if cause == "intake_busy":
        slot_reserved = rag_runtime._INTAKE_SLOT.acquire(blocking=False)
        assert slot_reserved, "a prior test leaked the global intake reservation"
    else:
        claim = runtime.raw_capture.claim()
        assert claim is not None
        if cause == "retry_not_due":
            runtime.raw_capture.fail(claim, retry_seconds=3600)
    task = asyncio.create_task(runtime.drain_raw_once())
    try:
        async with asyncio.timeout(2):
            with pytest.raises(AssertionError, match="drain_done=True, drain_result=0"):
                await require_compaction_started(runtime, task, asyncio.Event())
        assert model.calls == 0
        status = runtime.raw_capture.status()
        assert status["pending_events"] == 1
        assert status["completed_events"] == 0
        assert status["leased_events"] == (cause == "already_leased")
        assert status["failed_attempts"] == (cause == "retry_not_due")
    finally:
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            if slot_reserved:
                rag_runtime._INTAKE_SLOT.release()
            if claim is not None:
                with runtime.raw_capture._connection() as connection:
                    connection.execute("UPDATE raw_events SET lease_until=0,available_at=0")
    assert await asyncio.wait_for(runtime.drain_raw_once(), timeout=5) == 1
    assert runtime.raw_capture.status()["completed_events"] == 1


@pytest.mark.asyncio
async def test_model_initialization_failure_before_generation_retains_and_recovers(raw_runtime, monkeypatch):
    from logchat.local import rag_runtime

    store, project, source = raw_runtime
    runtime = store.rag_runtime
    original = event(source=source["id"])
    await runtime.ingest_async(project["id"], source["id"], [original])
    model = await configure_mock(store, monkeypatch)
    attempts = 0

    def unavailable(**kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError(RAW)

    monkeypatch.setattr("pipeline.models.LocalModels", unavailable)
    task = asyncio.create_task(runtime.drain_raw_once())
    async with asyncio.timeout(2):
        with pytest.raises(AssertionError, match="drain_done=True, drain_result=0") as failure:
            await require_compaction_started(runtime, task, asyncio.Event())
    assert await task == 0
    assert attempts == 1 and model.calls == 0
    assert RAW not in str(failure.value)
    assert runtime.error == "compact_intake_unavailable_retry_required"
    status = runtime.raw_capture.status()
    assert status["pending_events"] == status["failed_attempts"] == 1
    assert status["completed_events"] == status["leased_events"] == 0
    assert status["pending_failure_categories"] == [runtime.error]
    assert runtime.model_profile._generators == {}  # Failed construction is not cached.
    assert rag_runtime._INTAKE_SLOT.acquire(blocking=False), "initialization failure leaked intake slot"
    rag_runtime._INTAKE_SLOT.release()
    with runtime.backend.connect() as connection:
        assert connection.execute("SELECT count(*) FROM rag_schedule_jobs").fetchone()[0] == 0

    monkeypatch.setattr("pipeline.models.LocalModels", lambda **kwargs: model)
    with runtime.raw_capture._connection() as connection:
        connection.execute("UPDATE raw_events SET available_at=0")
    assert await asyncio.wait_for(runtime.drain_raw_once(), timeout=5) == 1
    assert (await runtime.ingest_async(project["id"], source["id"], [original]))["deduplicated"] == 1
    status = runtime.raw_capture.status()
    assert status["pending_events"] == 0 and status["completed_events"] == 1
    assert status["accepted_events"] == 1
    with runtime.backend.connect() as connection:
        assert connection.execute("SELECT count(*) FROM rag_schedule_jobs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_compaction_start_check_reports_escaped_exception_without_payload(raw_runtime, monkeypatch):
    runtime = raw_runtime[0].rag_runtime

    def broken_configuration():
        raise ValueError(RAW)

    monkeypatch.setattr(runtime, "_refresh_configuration", broken_configuration)
    task = asyncio.create_task(runtime.drain_raw_once())
    async with asyncio.timeout(2):
        with pytest.raises(AssertionError, match="drain_done=True, drain_result=raised:ValueError") as failure:
            await require_compaction_started(runtime, task, asyncio.Event())
    assert RAW not in str(failure.value)
    assert task.done() and isinstance(task.exception(), ValueError)
