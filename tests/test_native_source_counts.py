"""Source readiness reflects published, scoped memory rather than queued intake."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json

from logchat.local.raw_capture import save_capture_policy
from logchat.local.rag_runtime import NativeRAGRuntime
from logchat.local.store import LocalStore
from logchat.rag.builder import prepare_batch
from logchat.rag.contracts import BuildResult, EmbeddedChunk, EmbeddingSpec, TimeWindow
from pipeline.types import LogEvent


NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
SPEC = EmbeddingSpec("ollama", "test-embedding", "test-digest:prefix-v1", 3)


def event(source, identifier, *, offset=0):
    return LogEvent(identifier, NOW + timedelta(seconds=offset), source["id"],
                    "api", "error", "Session renewal refused", identifier)


def enable_native(store):
    (store.state_dir / "rag.json").write_text(json.dumps({
        "base_url": "http://127.0.0.1:11434", "embedding": asdict(SPEC),
        "generation_model": "test-generation", "content_policy": "redacted_templates",
    }))
    store.rag_runtime = NativeRAGRuntime(store)
    return store.rag_runtime


def prepared(identity, source, identifier, *, offset=0):
    start = NOW + timedelta(seconds=offset)
    return prepare_batch(identity, [event(source, identifier, offset=offset)],
                         TimeWindow(start, start + timedelta(seconds=1)))


def publish(runtime, batch):
    result = BuildResult(batch.batch_id, tuple(
        EmbeddedChunk(chunk, (1., .2, .3), SPEC) for chunk in batch.chunks), batch.coverage)
    connection = runtime.backend.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        runtime.backend.write_result(connection, result)
        connection.commit()
    finally:
        connection.close()


def test_native_source_counts_published_chunks_with_complete_scope_and_legacy_separation(tmp_path):
    store = LocalStore(tmp_path)
    project = store.create_project("native source counts")
    store.create_environment(project["id"], "preview")
    first = store.create_source(project["id"], "first", "dev", "push", None)
    second = store.create_source(project["id"], "second", "dev", "push", None)
    preview = store.create_source(project["id"], "preview", "preview", "push", None)
    empty = store.create_source(project["id"], "unindexed", "dev", "push", None)
    store.ingest(project["id"], first["id"], [event(first, "old-lexical")])
    runtime = enable_native(store)
    identity = runtime.identity(project["id"], first["id"])
    publish(runtime, prepared(identity, first, "first-a"))
    publish(runtime, prepared(identity, first, "first-b", offset=1))
    publish(runtime, prepared(runtime.identity(project["id"], second["id"]), second, "second"))
    publish(runtime, prepared(runtime.identity(project["id"], preview["id"]), preview, "preview"))
    # Same source ID cannot make another principal/environment's rows ours.
    for field, value in (("owner_id", "other-owner"), ("project_id", "other-project"),
                         ("environment_id", "other-environment"), ("source_id", "unknown-source")):
        publish(runtime, prepared(replace(identity, **{field: value}), first, "foreign-" + field))
    runtime.scheduler.register_source(identity, interval_seconds=5)
    runtime.scheduler.enqueue(prepared(identity, first, "queued-only", offset=2))

    sources = {source["id"]: source for source in store.list_sources(project["id"])}
    assert {key: source["chunk_count"] for key, source in sources.items()} == {
        first["id"]: 2, second["id"]: 1, preview["id"]: 1, empty["id"]: 0,
    }
    assert sources[first["id"]]["legacy_chunk_count"] == 1
    assert all("environment_id" not in source for source in sources.values())
    # Both status paths call list_sources; neither should recurse into the other.
    assert store.status(project["id"])["sources"] == list(sources.values())
    assert runtime.status(project["id"])["sources"]


def test_legacy_source_counts_preserved_without_native_runtime(tmp_path):
    store = LocalStore(tmp_path)
    project = store.create_project("legacy counts")
    source = store.create_source(project["id"], "legacy", "dev", "push", None)
    store.ingest(project["id"], source["id"], [event(source, "legacy-event")])
    listed = store.list_sources(project["id"])[0]
    assert listed["chunk_count"] == 1
    assert "legacy_chunk_count" not in listed


def test_capture_only_source_counts_do_not_present_originals_or_legacy_as_native(tmp_path):
    old = LocalStore(tmp_path)
    project = old.create_project("capture-only counts")
    source = old.create_source(project["id"], "capture", "dev", "push", None)
    old.ingest(project["id"], source["id"], [event(source, "historical-lexical")])
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    store = LocalStore(tmp_path)
    assert store.rag_runtime.backend is None
    store.ingest(project["id"], source["id"], [event(source, "pending-original")])
    assert store.rag_runtime.raw_capture.status(project["id"])["pending_events"] == 1
    listed = store.list_sources(project["id"])[0]
    assert listed["chunk_count"] == 0
    assert listed["legacy_chunk_count"] == 1
