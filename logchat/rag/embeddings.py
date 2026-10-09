"""Digest-bound Ollama embeddings with complete bounded inputs and safe errors."""
from __future__ import annotations

import math
import json
from urllib.parse import urlparse

import httpx

from logchat.rag.contracts import EmbeddingSpec, MAX_SUMMARY_CHARS
from pipeline.models import ModelUnavailable
from pipeline.redaction import redact_text

MAX_TEXTS = 16
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PREFIX_VERSION = "prefix-v1"


class OllamaEmbeddingProvider:
    def __init__(self, base_url="http://127.0.0.1:11434", *, spec, transport=None, preserve_content=False):
        parsed = urlparse(base_url)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname not in
                {"127.0.0.1", "localhost", "::1", "ollama", "host.docker.internal"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("local_embedding_endpoint_required")
        if spec.provider != "ollama":
            raise ValueError("ollama_embedding_spec_required")
        if preserve_content and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Preserved local embeddings require a loopback endpoint.")
        self.preserve_content = preserve_content
        self.local_only = True
        self.base_url = base_url.rstrip("/")
        self.spec = spec
        self.transport = transport

    @classmethod
    async def create(cls, base_url="http://127.0.0.1:11434", *, model="nomic-embed-text", dimensions=768, transport=None, preserve_content=False):
        if not isinstance(model, str) or not model or len(model) > 200 or any(c.isspace() for c in model):
            raise ValueError("invalid_embedding_model_name")
        # Constructor validates the endpoint before any network request.
        value = cls(base_url, spec=EmbeddingSpec("ollama", model, "unresolved", dimensions), transport=transport,preserve_content=preserve_content)
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=transport, timeout=10) as client:
                data = await value._request(client, "GET", "/api/tags")
                matches = [m for m in data["models"] if m.get("name") in {model, model + ":latest"}]
                if len(matches) != 1 or not isinstance(matches[0].get("digest"), str) or not 1 <= len(matches[0]["digest"]) <= 200:
                    raise ValueError()
                digest = matches[0]["digest"]
        except Exception:
            raise ModelUnavailable("embedding_model_digest_unavailable") from None
        value.spec = EmbeddingSpec("ollama", model, digest + ":" + PREFIX_VERSION, dimensions)
        return value

    async def embed(self, texts, *, purpose="document"):
        if purpose not in {"document", "query"} or not 1 <= len(texts) <= MAX_TEXTS:
            raise ValueError("embedding_batch_bound_exceeded")
        maximum = MAX_SUMMARY_CHARS if self.preserve_content else 4000
        if any(not isinstance(text, str) or not text.strip() or len(text) > maximum for text in texts):
            raise ValueError("embedding_text_bound_exceeded")
        prefix = ("search_document: " if purpose == "document" else "search_query: ") if self.spec.model.split(":")[0] == "nomic-embed-text" else ""
        payload = [prefix + (text if self.preserve_content else redact_text(text)) for text in texts]
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=self.transport, timeout=120) as client:
                await self._verify_revision(client)
                data = await self._request(client, "POST", "/api/embed", payload={
                    "model": self.spec.model, "input": payload, "truncate": False,
                })
                await self._verify_revision(client)
            vectors = data["embeddings"]
            if len(vectors) != len(texts):
                raise ValueError()
            for vector in vectors:
                if (len(vector) != self.spec.dimensions or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector)
                        or not any(x != 0 for x in vector)):
                    raise ValueError()
            return tuple(tuple(float(x) for x in vector) for vector in vectors)
        except Exception:
            raise ModelUnavailable("embedding_unavailable_or_invalid_response") from None

    async def _verify_revision(self, client):
        data = await self._request(client, "GET", "/api/tags")
        matches = [m for m in data["models"] if m.get("name") in {self.spec.model, self.spec.model + ":latest"}]
        if (len(matches) != 1 or matches[0].get("digest", "") + ":" + PREFIX_VERSION != self.spec.revision):
            raise ValueError("embedding_model_revision_changed")

    async def _request(self, client, method, endpoint, *, payload=None):
        async with client.stream(method, self.base_url + endpoint, json=payload) as response:
            response.raise_for_status()
            content = bytearray()
            async for part in response.aiter_bytes():
                if len(content) + len(part) > MAX_RESPONSE_BYTES:
                    raise ValueError("embedding_response_bound_exceeded")
                content.extend(part)
        return json.loads(content)
