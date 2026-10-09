"""Real SQLite integration; deterministic mocked embedding transport."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from logchat.local.rag_runtime import configure
from logchat.local.store import LocalStore
from logchat.rag.contracts import EmbeddingSpec, RetrievalCell, TimeWindow
from logchat.rag.embeddings import OllamaEmbeddingProvider
from pipeline.types import LogEvent


class MockEmbeddings:
    spec = EmbeddingSpec("ollama","mock-model","mock-digest:prefix-v1",8)

    async def embed(self, texts, *, purpose="document"):
        return [(1.0,0.1,0.2,0.3,0.4,0.5,0.6,0.7) for _ in texts]


@pytest.mark.asyncio
async def test_prepared_intake_worker_publish_restart_and_replay(tmp_path, monkeypatch):
    async def create(**kwargs):
        return MockEmbeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",create)
    await configure(tmp_path,chat_model="mock-generation", model="mock-model",dimensions=8)
    store=LocalStore(tmp_path)
    project=store.create_project("synthetic runtime")
    source=store.create_source(project["id"],"stdout","dev","push",None)
    runtime=store.rag_runtime
    now=datetime.now(timezone.utc)
    event=LogEvent("synthetic-1",now,source["id"],"test","warning",
                   "Session credential expired token=synthetic_private_canary", "synthetic",duration_ms=42)
    accepted=store.ingest(project["id"],source["id"],[event])
    assert accepted["state"]=="durably_prepared" and accepted["indexed"] is False
    runtime.scheduler.dispatch(now=now+timedelta(seconds=10))
    await runtime.start()
    try:
        async with asyncio.timeout(10):
            while runtime.backend.statistics()["vectors"]!=1:
                await asyncio.sleep(.01)
    finally:
        await runtime.stop()
    reopened=LocalStore(tmp_path)
    repeated=reopened.ingest(project["id"],source["id"],[event])
    assert repeated["jobs"]==accepted["jobs"]
    assert reopened.rag_runtime.backend.statistics()["vectors"]==1
    identity=runtime.identity(project["id"],source["id"])
    cell=RetrievalCell("test",identity.owner_id,identity.project_id,identity.environment_id,
                       TimeWindow(now-timedelta(seconds=1),now+timedelta(seconds=1)))
    hit=reopened.rag_runtime.backend.vector_search(cell,(1,.1,.2,.3,.4,.5,.6,.7),limit=1)[0]
    assert "synthetic_private_canary" not in hit.chunk.summary
    assert hit.chunk.metrics.duration_sum_ms==42
    assert reopened.status(project["id"])["memory"]["application"]["retrieval"]=="semantic_hybrid"
    assert reopened.rag_runtime.backend.get_chunk(hit.chunk.chunk_id,owner_id="local-os-user",project_id="wrong") is None
    assert len(reopened.rag_runtime.backend.list_chunks(owner_id="local-os-user",project_id=project["id"])) == 1
    assert not reopened.rag_runtime.backend.list_chunks(owner_id="local-os-user",project_id="wrong")


@pytest.mark.asyncio
async def test_index_model_change_rejected_without_replacing_configuration(tmp_path,monkeypatch):
    async def create(**kwargs): return MockEmbeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",create)
    await configure(tmp_path,chat_model="mock-generation", model="mock-model",dimensions=8)
    original=(tmp_path/"rag.json").read_bytes()
    class Different(MockEmbeddings):
        spec=EmbeddingSpec("ollama","mock-model","different-digest",8)
    async def different(**kwargs):return Different()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",different)
    with pytest.raises(ValueError,match="configuration differs"):
        await configure(tmp_path,chat_model="mock-generation", model="mock-model",dimensions=8)
    assert (tmp_path/"rag.json").read_bytes()==original


@pytest.mark.asyncio
async def test_authenticated_retry_recovers_exhausted_source_without_deleting_work(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from logchat.local.app import create_app
    async def create(**kwargs):return MockEmbeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",create)
    await configure(tmp_path,chat_model="mock-generation", model="mock-model",dimensions=8)
    application=create_app(tmp_path,8772,capture_host=False)
    store=application.state.store
    project=store.create_project("retry")
    source=store.create_source(project["id"],"test","dev","push",None)
    now=datetime.now(timezone.utc)
    event=LogEvent("retry-1",now,source["id"],"test","error","Credential expired","test")
    store.ingest(project["id"],source["id"],[event])
    queue=store.rag_runtime.scheduler
    queue.dispatch(now=now+timedelta(seconds=10))
    for attempt in range(3):
        instant=now+timedelta(seconds=10+10*attempt)
        job=queue.claim_jobs(now=instant)[0]
        assert queue.fail(job,"embedding_unavailable",now=instant)
    assert not queue.claim_jobs(now=now+timedelta(minutes=1))
    client=TestClient(application,base_url="http://127.0.0.1:8772")
    route=f"/projects/{project['id']}/retry"
    assert client.post(route).status_code==401
    result=client.post(route,headers={"Authorization":"Bearer "+store.control_token})
    assert result.status_code==200 and result.json()["retried"]==1
    assert queue.claim_jobs()[0].batch.chunks[0].summary


@pytest.mark.asyncio
async def test_revision_mismatch_is_unavailable_not_invalid_scope(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from logchat.local.app import create_app
    async def create(**kwargs):return MockEmbeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",create)
    await configure(tmp_path,chat_model="mock-generation", model="mock-model",dimensions=8)
    application=create_app(tmp_path,8772,capture_host=False)
    store=application.state.store;project=store.create_project("mismatch")
    environment=store.list_environments(project["id"])[0]
    class Different(MockEmbeddings):spec=EmbeddingSpec("ollama","mock-model","changed",8)
    async def changed(**kwargs):return Different()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",changed)
    client=TestClient(application,base_url="http://127.0.0.1:8772")
    response=client.post(f"/projects/{project['id']}/ask",json={"question":"What happened?","environment_ids":[environment['id']]},headers={"Authorization":"Bearer "+store.control_token})
    assert response.status_code==503
    response=client.get(f"/projects/{project['id']}/search",params={"q":"What happened?"},headers={"Authorization":"Bearer "+store.control_token})
    assert response.status_code==503  # Never silently switches to legacy lexical storage.
    response=client.get(f"/projects/{project['id']}/search",headers={"Authorization":"Bearer "+store.control_token})
    assert response.status_code==200 and response.json()["retrieval"]=="memory_browse"


@pytest.mark.asyncio
async def test_enabling_semantic_memory_preserves_legacy_inspector(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from logchat.local.app import create_app
    legacy=LocalStore(tmp_path);project=legacy.create_project("legacy")
    source=legacy.create_source(project["id"],"test","dev","push",None)
    legacy.ingest(project["id"],source["id"],[LogEvent("old",datetime.now(timezone.utc),source["id"],"test","error","request timed out","test")])
    identifier=legacy.evidence(project["id"],"")[0]["id"]
    async def create(**kwargs):return MockEmbeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider,"create",create)
    await configure(tmp_path,chat_model="mock-generation", model="mock-model",dimensions=8)
    application=create_app(tmp_path,8772,capture_host=False)
    client=TestClient(application,base_url="http://127.0.0.1:8772")
    response=client.get(f"/projects/{project['id']}/memories/{identifier}",headers={"Authorization":"Bearer "+legacy.control_token})
    assert response.status_code==200 and response.json()["memory"]["id"]==identifier


def test_foreground_fresh_start_requires_explicit_model_setup(tmp_path, monkeypatch):
    from logchat.local.cli import start_command
    import typer
    with pytest.raises(typer.Exit):
        start_command(8772, tmp_path, True)
    assert not (tmp_path / "rag.json").exists()
