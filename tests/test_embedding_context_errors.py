"""Safe provider categories and terminal all-or-nothing worker failures."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from logchat.local.rag_runtime import configure
from logchat.local.store import LocalStore
from logchat.rag.contracts import EmbeddingSpec
from logchat.rag.embeddings import EmbeddingContextExceeded, OllamaEmbeddingProvider
from pipeline.models import ModelUnavailable
from pipeline.types import LogEvent

SPEC = EmbeddingSpec("ollama", "mock-model", "fixture:prefix-v1", 8)
OVERFLOW = "the input length exceeds the context length"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [OVERFLOW, "input length exceeds maximum context length"])
async def test_exact_overflow_category_and_full_request(message):
    bodies = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": SPEC.model, "digest": "fixture"}]})
        bodies.append(json.loads(request.content))
        return httpx.Response(400, json={"error": message})

    provider = OllamaEmbeddingProvider(spec=SPEC, transport=httpx.MockTransport(handler), preserve_content=True)
    text = "punctuation_" * 900 + "完整_SUFFIX"
    with pytest.raises(EmbeddingContextExceeded) as caught:
        await provider.embed([text])
    assert str(caught.value) == caught.value.category == "embedding_input_context_exceeded"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert bodies == [{"model": SPEC.model, "input": [text], "truncate": False}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (400, {"error": "PRIVATE_UPSTREAM_CANARY"}),
    (400, {"error": OVERFLOW + " PRIVATE_UPSTREAM_CANARY"}),
    (500, {"error": OVERFLOW}),
    (400, {"error": [OVERFLOW]}),
    (400, [OVERFLOW]),
    (400, b"PRIVATE_UPSTREAM_CANARY not JSON"),
    (400, {"error": OVERFLOW, "padding": "x" * 1024}),
    (200, {"embeddings": [[True] * 8]}),
    (200, b"PRIVATE_UPSTREAM_CANARY not JSON"),
])
async def test_unrecognized_errors_remain_generic(status, body):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": SPEC.model, "digest": "fixture"}]})
        return httpx.Response(status, content=body) if isinstance(body, bytes) else httpx.Response(status, json=body)

    provider = OllamaEmbeddingProvider(spec=SPEC, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelUnavailable) as caught:
        await provider.embed(["synthetic input"])
    assert str(caught.value) == "embedding_unavailable_or_invalid_response"
    assert getattr(caught.value, "category", None) is None
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_tags_400_cannot_be_classified_as_input_overflow():
    provider = OllamaEmbeddingProvider(spec=SPEC, transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": OVERFLOW})))
    with pytest.raises(ModelUnavailable) as caught:
        await provider.embed(["synthetic input"])
    assert not isinstance(caught.value, EmbeddingContextExceeded)


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", [True, False])
async def test_worker_terminal_overflow_partial_vectors_restart_and_explicit_retry(tmp_path, monkeypatch, overflow):
    calls = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": SPEC.model, "digest": "fixture"}]})
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 2:
            return httpx.Response(400, json={"error": OVERFLOW if overflow else "PRIVATE_UPSTREAM_CANARY"})
        return httpx.Response(200, json={"embeddings": [[1.] + [0.] * 7 for _ in body["input"]]})

    async def create(**kwargs):
        return OllamaEmbeddingProvider(spec=SPEC, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(OllamaEmbeddingProvider, "create", create)
    await configure(tmp_path, chat_model="mock-generation", model=SPEC.model, dimensions=8)
    store = LocalStore(tmp_path)
    project = store.create_project("synthetic context failure")
    source = store.create_source(project["id"], "test", "dev", "push", None)
    now = datetime.now(timezone.utc)
    events = [LogEvent(f"synthetic-{i}", now, source["id"], "test", "error",
                       "pattern" + chr(0x4E00 + i), "test") for i in range(17)]
    store.ingest(project["id"], source["id"], events)
    runtime = store.rag_runtime
    queue = runtime.scheduler
    identity = runtime.identity(project["id"], source["id"])
    queue.dispatch(now=now + timedelta(seconds=10))
    await runtime.start()
    try:
        async with asyncio.timeout(10):
            while runtime.error is None:
                await asyncio.sleep(.01)
        assert runtime.error == ("embedding_input_context_exceeded" if overflow else "embedding_unavailable")
    finally:
        await runtime.stop()
    assert len(calls) == 2
    assert all(body["truncate"] is False for body in calls)
    assert runtime.backend.statistics()["vectors"] == 0
    assert queue.status(identity)["jobs"].get("failed" if overflow else "deferred") == 1
    reopened = LocalStore(tmp_path).rag_runtime
    if overflow:
        assert not reopened.scheduler.claim_jobs(now=now + timedelta(days=1))
        assert reopened.scheduler.retry_failed(identity) == 1
    job = reopened.scheduler.claim_jobs(now=now + timedelta(days=1))[0]
    assert len(job.batch.chunks) == 17
    # Recover through normal embedding and atomic completion, with no generation.
    from logchat.rag.builder import embed_batch
    result = await embed_batch(job.batch, await create())
    assert reopened.scheduler.complete(job, result, writer=reopened.backend.write_result,
                                      now=now + timedelta(days=1))
    assert reopened.backend.statistics()["vectors"] == 17
    assert not reopened.scheduler.claim_jobs(now=now + timedelta(days=2))
