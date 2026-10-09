"""Shared settings use real local SQLite and mocked local model calls only."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json

import pytest
import pytest_asyncio

from logchat.local import installer, rag_runtime, shared_settings
from logchat.local.raw_capture import save_capture_policy
from logchat.local.store import LocalStore
from logchat.rag.contracts import EmbeddingSpec
from logchat.rag.embeddings import OllamaEmbeddingProvider
from pipeline.types import LogEvent

PRIVATE = "PRIVATE_PROVIDER_RESPONSE"


class Embeddings:
    preserve_content = True
    local_only = True

    def __init__(self, model, dimensions, revision):
        self.spec = EmbeddingSpec("ollama", model, revision, dimensions)

    async def embed(self, texts, **kwargs):
        return [tuple([1.] + [.1] * (self.spec.dimensions - 1)) for _ in texts]


class Generation:
    local_only = True
    preserve_content = True

    def __init__(self, name, state):
        self.chat_model, self.state = name, state

    async def installed(self):
        return {"chat": self.chat_model != self.state.get("missing"), "embedding": True}

    async def generate(self, instruction, context, schema):
        self.state["calls"].append((self.chat_model, context))
        if self.chat_model == self.state.get("failing"):
            raise RuntimeError(PRIVATE)
        if "marker" in context:
            if self.state.get("probe_started") is not None:
                self.state["probe_started"].set()
                await self.state["probe_release"].wait()
            return {"marker": installer.READINESS_MARKER, "ready": True}
        if "items" in context:
            if self.state.get("intake_started") is not None:
                self.state["intake_started"].set()
                await self.state["intake_release"].wait()
            return {"sections": [[item["ref"] for item in context["items"]]]}
        return {"summary": "The service reported request failures.", "important_fields": [], "uncertainties": []}


@pytest.fixture
def models(monkeypatch):
    state = {"calls": [], "requests": [], "embedding_calls": [], "revision": "stable-embedding-revision",
             "metadata": {"old:7b": {"capabilities": ["completion"]},
                          "new:7b": {"capabilities": ["completion", "tools"]},
                          "nomic-embed-text:latest": {"capabilities": ["embedding"]},
                          "hosted:cloud": {"capabilities": ["completion"], "remote_host": "https://remote.invalid"}}}
    async def request(client, method, endpoint, *, payload=None):
        state["requests"].append((method, endpoint, payload))
        if endpoint.endswith("/api/tags"):
            return {"models": [{"name": name} for name in state["metadata"]]}
        return state["metadata"].get(payload["model"], {"capabilities": []})
    async def create(**kwargs):
        state["embedding_calls"].append(kwargs)
        return Embeddings(kwargs["model"], kwargs["dimensions"], state["revision"])
    def generation(**kwargs):
        return Generation(kwargs["chat_model"], state)
    monkeypatch.setattr(shared_settings, "_request", request)
    monkeypatch.setattr(OllamaEmbeddingProvider, "create", create)
    monkeypatch.setattr(installer, "LocalModels", generation)
    monkeypatch.setattr("pipeline.models.LocalModels", generation)
    return state


@pytest_asyncio.fixture
async def configured(tmp_path, models):
    await rag_runtime.configure(tmp_path, model="nomic-embed-text", dimensions=8,
                                chat_model="old:7b", content_policy="local_model_compact")
    store = LocalStore(tmp_path)
    project = store.create_project("shared settings")
    source = store.create_source(project["id"], "logs", "dev", "push", None)
    return store, project, source


def event(source, identifier):
    return LogEvent(identifier, datetime.now(timezone.utc), source["id"], "web", "error",
                    "Request failed error_code=FAIL42", "fingerprint", request_status=503)


@pytest.mark.asyncio
async def test_listing_only_local_generation_candidates_and_never_probes_generation(configured, models):
    store, _, _ = configured
    result = await shared_settings.list_models(store)
    assert [row["name"] for row in result["models"]] == ["old:7b", "new:7b"]
    assert result["models"][0]["selected"] is True
    assert result["models"][1]["readiness"] == "not_checked"
    assert result["omitted_models"] == 2 and not result["truncated"]
    assert result["configured_model"] == "old:7b"
    assert models["calls"] == []
    assert {url.split('/api/')[0] for _, url, _ in models["requests"]} == {"http://127.0.0.1:11434"}


@pytest.mark.asyncio
async def test_listing_unconfigured_uses_only_default_endpoint(tmp_path, models):
    store = LocalStore(tmp_path)
    result = await shared_settings.list_models(store)
    assert result["configured_model"] is None and result["endpoint"] == installer.DEFAULT_ENDPOINT
    assert not (tmp_path / "rag.json").exists()
    assert store.rag_runtime is None


@pytest.mark.asyncio
async def test_success_switches_all_roles_keeps_embedding_and_historical_provenance(configured, models):
    store, project, source = configured
    runtime = store.rag_runtime
    first = await runtime.ingest_async(project["id"], source["id"], [event(source, "before")])
    backend, scheduler, spec, old_profile = runtime.backend, runtime.scheduler, runtime.spec, runtime.model_profile
    with backend.connect() as connection:
        old_payload = connection.execute("SELECT payload FROM rag_schedule_jobs WHERE job_id=?", (first["jobs"][0],)).fetchone()[0]
    result = await shared_settings.select_model(store, None, "new:7b", embedding_model="nomic-embed-text", dimensions=8)
    assert result["shared_model"] == "new:7b" and result["embedding_unchanged"]
    assert runtime.backend is backend and runtime.scheduler is scheduler and runtime.spec == spec
    assert runtime.model_profile is not old_profile and store.rag_runtime is runtime
    assert runtime.model_profile.generation("chunking") is runtime.model_profile.generation("summary") is runtime.model_profile.generation("relevance")
    assert runtime.model_profile.generation().chat_model == "new:7b"
    second = await runtime.ingest_async(project["id"], source["id"], [event(source, "after")])
    with backend.connect() as connection:
        assert connection.execute("SELECT payload FROM rag_schedule_jobs WHERE job_id=?", (first["jobs"][0],)).fetchone()[0] == old_payload
        payload = json.loads(connection.execute("SELECT payload FROM rag_schedule_jobs WHERE job_id=?", (second["jobs"][0],)).fetchone()[0])
    assert payload["chunks"][0]["summary_model"] == "new:7b"
    assert json.loads(old_payload)["chunks"][0]["summary_model"] == "old:7b"
    assert json.loads((store.state_dir / "rag.json").read_text())["embedding"] == asdict(spec)
    assert not list(store.state_dir.glob(".model-check-*"))


@pytest.mark.asyncio
async def test_busy_intake_cannot_change_model_mid_batch(configured, models):
    store, project, source = configured
    models["intake_started"], models["intake_release"] = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(store.rag_runtime.ingest_async(project["id"], source["id"], [event(source, "busy")]))
    try:
        await models["intake_started"].wait()
        with pytest.raises(shared_settings.SettingsError) as error:
            await shared_settings.select_model(store, None, "new:7b", embedding_model="nomic-embed-text", dimensions=8)
        assert error.value.status_code == 409
        assert store.rag_runtime.model_profile.generation_model == "old:7b"
        assert models["requests"] == []
    finally:
        models["intake_release"].set()
        await task


@pytest.mark.asyncio
async def test_selection_holds_preparation_slot_but_allows_raw_capture(configured, models):
    store, project, source = configured
    save_capture_policy(store.state_dir, mode="retain_until_summarized")
    models["probe_started"], models["probe_release"] = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(shared_settings.select_model(store, None, "new:7b", embedding_model="nomic-embed-text", dimensions=8))
    try:
        await models["probe_started"].wait()
        result = await store.rag_runtime.ingest_async(project["id"], source["id"], [event(source, "captured-during-check")])
        assert result["state"] == "raw_pending"
        with pytest.raises(shared_settings.SettingsError) as error:
            await shared_settings.select_model(store, None, "old:7b")
        assert error.value.status_code == 409
        assert await store.rag_runtime.drain_raw_once() == 0
    finally:
        models["probe_release"].set()
        await task
    assert await store.rag_runtime.drain_raw_once() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["readiness", "embedding_revision", "unsupported_model"])
async def test_selection_failure_preserves_exact_config_runtime_and_history(configured, models, failure):
    store, project, source = configured
    runtime = store.rag_runtime
    await runtime.ingest_async(project["id"], source["id"], [event(source, "retained")])
    original = (store.state_dir / "rag.json").read_bytes()
    profile, backend = runtime.model_profile, runtime.backend
    with backend.connect() as connection:
        rows = [tuple(row) for row in connection.execute("SELECT job_id,payload FROM rag_schedule_jobs")]
    if failure == "readiness":
        models["failing"] = "new:7b"
    elif failure == "embedding_revision":
        models["revision"] = "incompatible-new-revision"
    else:
        models["metadata"]["new:7b"] = {"capabilities": ["embedding"]}
    with pytest.raises(shared_settings.SettingsError) as error:
        await shared_settings.select_model(store, "http://127.0.0.1:11435", "new:7b")
    assert error.value.status_code == {"readiness": 503, "embedding_revision": 409, "unsupported_model": 422}[failure]
    assert PRIVATE not in str(error.value)
    assert (store.state_dir / "rag.json").read_bytes() == original
    assert runtime.model_profile is profile and runtime.backend is backend
    with backend.connect() as connection:
        assert [tuple(row) for row in connection.execute("SELECT job_id,payload FROM rag_schedule_jobs")] == rows
    assert rag_runtime._INTAKE_SLOT.acquire(blocking=False)
    rag_runtime._INTAKE_SLOT.release()


@pytest.mark.asyncio
async def test_endpoint_change_requires_same_embedding_revision(configured, models):
    store, _, _ = configured
    spec = store.rag_runtime.spec
    result = await shared_settings.select_model(store, "http://localhost:11435/", "new:7b")
    assert result["endpoint"] == "http://localhost:11435"
    assert store.rag_runtime.spec == spec
    assert models["embedding_calls"][-1]["base_url"] == "http://localhost:11435"


@pytest.mark.asyncio
async def test_raw_only_bootstrap_keeps_pending_then_uses_explicit_selected_model(tmp_path, models):
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    store = LocalStore(tmp_path)
    runtime = store.rag_runtime
    project = store.create_project("capture")
    source = store.create_source(project["id"], "logs", "dev", "push", None)
    await runtime.ingest_async(project["id"], source["id"], [event(source, "pending")])
    result = await shared_settings.select_model(store, installer.DEFAULT_ENDPOINT, "new:7b", embedding_model="nomic-embed-text", dimensions=8)
    assert result["runtime_created"] is False and result["embedding_unchanged"] is False
    assert runtime is store.rag_runtime and runtime.model_profile.generation_model == "new:7b"
    assert runtime.raw_capture.status()["pending_events"] == 1
    assert await runtime.drain_raw_once() == 1
    assert runtime.raw_capture.status()["pending_events"] == 0


@pytest.mark.asyncio
async def test_fresh_bootstrap_creates_runtime_without_starting_background_work(tmp_path, models):
    store = LocalStore(tmp_path)
    result = await shared_settings.select_model(store, None, "new:7b", embedding_model="nomic-embed-text", dimensions=8)
    assert result["runtime_created"] is True
    assert store.rag_runtime.model_profile.generation_model == "new:7b"
    assert store.rag_runtime.task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,model", [("https://provider.invalid", "new:7b"),
    ("http://key:secret@localhost:11434", "new:7b"), (None, ""), (None, None), (None, "with spaces")])
async def test_invalid_or_implicit_choices_do_not_contact_models(configured, models, endpoint, model):
    store, _, _ = configured
    with pytest.raises(shared_settings.SettingsError) as error:
        await shared_settings.select_model(store, endpoint, model)
    assert error.value.status_code == 422 and "secret" not in str(error.value)
    assert models["requests"] == []


@pytest.mark.asyncio
async def test_atomic_write_failure_leaves_profile_and_file_unchanged(configured, models, monkeypatch):
    store, _, _ = configured
    original = (store.state_dir / "rag.json").read_bytes()
    profile = store.rag_runtime.model_profile
    write = shared_settings._atomic_write
    def rejected(path, data):
        if path.parent == store.state_dir:
            raise OSError(PRIVATE)
        write(path, data)
    monkeypatch.setattr(shared_settings, "_atomic_write", rejected)
    with pytest.raises(shared_settings.SettingsError) as error:
        await shared_settings.select_model(store, None, "new:7b", embedding_model="nomic-embed-text", dimensions=8)
    assert error.value.status_code == 503 and PRIVATE not in str(error.value)
    assert (store.state_dir / "rag.json").read_bytes() == original
    assert store.rag_runtime.model_profile is profile


@pytest.mark.asyncio
async def test_cancelled_probe_keeps_profile_and_releases_slot(configured, models):
    store, _, _ = configured
    profile = store.rag_runtime.model_profile
    original = (store.state_dir / "rag.json").read_bytes()
    models["probe_started"], models["probe_release"] = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(shared_settings.select_model(store, None, "new:7b", embedding_model="nomic-embed-text", dimensions=8))
    await models["probe_started"].wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.rag_runtime.model_profile is profile
    assert (store.state_dir / "rag.json").read_bytes() == original
    assert rag_runtime._INTAKE_SLOT.acquire(blocking=False)
    rag_runtime._INTAKE_SLOT.release()


@pytest.mark.asyncio
async def test_old_inflight_provider_cannot_overwrite_selected_connection(configured, models, monkeypatch):
    from pipeline.models import ModelUnavailable
    store, _, _ = configured
    runtime = store.rag_runtime
    started, release = asyncio.Event(), asyncio.Event()
    old_profile = runtime.model_profile
    old_embeddings = old_profile.embeddings
    async def delayed_embeddings():
        provider = await old_embeddings()
        started.set()
        await release.wait()
        return provider
    monkeypatch.setattr(old_profile, "embeddings", delayed_embeddings)
    task = asyncio.create_task(runtime.connect_provider())
    try:
        await started.wait()
        await shared_settings.select_model(store, "http://localhost:11435", "new:7b")
    finally:
        release.set()
    with pytest.raises(ModelUnavailable, match="model_configuration_changed_retry_required"):
        await task
    assert runtime.provider is None
    await runtime.connect_provider()
    assert runtime.provider is not None
    assert models["embedding_calls"][-1]["base_url"] == "http://localhost:11435"
