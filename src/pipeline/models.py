"""Model clients with strict structured output and protected provider credentials."""
from __future__ import annotations

import json
import math
import os
import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from contextvars import ContextVar
import socket
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx

from cli.secrets import read_credential
from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json, generation_body
from pipeline.redaction import redact_text, sanitize
from pipeline.summary import observed_terms, render_summary


class ModelUnavailable(RuntimeError):
    pass


CONTEXT_CHARACTER_BUDGET = 24_000
MAX_LOCAL_RESPONSE_BYTES = 2 * 1024 * 1024
# Expand only after source/backend verification, not by guessing an earliest
# version or assuming future servers preserve these wire-field semantics.
VERIFIED_CONTEXT_GUARD_VERSIONS = frozenset({"0.34.4"})
NATIVE_CONTEXT_ERROR_PREFIX = "the prompt is longer than the context length currently available to the model;"
MAX_CONTEXT_ERROR_BYTES = 1024


class GenerationContextExceeded(ModelUnavailable):
    """The verified native tokenizer rejected the full rendered input."""



def _unique_json_object(pairs):
    """Reject ambiguous object keys before JSON decoding discards duplicates."""
    value = {}
    for key, child in pairs:
        if key in value:
            # Keys and values can contain private model/source content.
            raise ValueError("Duplicate JSON object key")
        value[key] = child
    return value


def validate_endpoint(value: str, *, local_http: bool = True) -> str:
    """Validate a user-authorized endpoint without allowing credential-bearing URLs."""
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        if not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        is_local = hostname.lower() in {"localhost", "host.docker.internal", "demo-mcp"}
        try:
            address = ip_address(hostname)
        except ValueError:
            address = None
        if address:
            is_local = address.is_loopback
            if not address.is_loopback and (address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved):
                raise ValueError()
        if hostname.lower() in {"metadata.google.internal", "metadata.google", "instance-data"}:
            raise ValueError()
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local_http and is_local):
            raise ValueError()
        if not parsed.netloc:
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Use HTTPS, or HTTP on localhost or host.docker.internal. URLs cannot contain credentials, query strings, or fragments.") from None
    return value.rstrip("/")


async def ensure_safe_resolution(value: str) -> None:
    """Reject DNS answers that target metadata/link-local/reserved network services."""
    parsed = urlparse(value)
    try:
        answers = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not answers:
            raise OSError()
        for answer in answers:
            address = ip_address(answer[4][0])
            if not address.is_loopback and (address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved):
                raise ValueError()
    except (OSError, ValueError):
        raise ModelUnavailable("Configured endpoint could not be reached safely.") from None


def _validate_schema(value, schema, path="response"):
    """Validate the JSON Schema subset used by structured generation."""
    expected = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected and not valid.get(expected, False):
        raise ValueError(f"{path} has the wrong type")
    if expected == "string" and "maxLength" in schema and len(value) > schema["maxLength"]:
        raise ValueError(f"{path} is too long")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if expected == "object":
        properties = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{path} is missing fields")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{path} has extra fields")
        for key, child in value.items():
            if key in properties:
                _validate_schema(child, properties[key], f"{path}.{key}")
    elif expected == "array":
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} has too many items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError(f"{path} has duplicate items")
        for index, child in enumerate(value):
            _validate_schema(child, schema.get("items", {}), f"{path}[{index}]")
    return value


@dataclass(frozen=True)
class _LocalRequestBinding:
    """Task-scoped routing and exact generation bytes; never read routing after await."""
    endpoint: str
    transport: object
    model: str
    embedding: bool = False
    request_bytes: bytes | None = None
    counter: object = None
    identity: object = None

    def check(self, owner, endpoint=None, body=None):
        from pipeline.native_tokenizer.ollama import incompatible
        model = owner.embedding_model if self.embedding else owner.chat_model
        parsed = urlparse(self.endpoint)
        if (parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise incompatible("same-host loopback required")
        if (owner.base_url != self.endpoint or owner.transport is not self.transport
                or model != self.model):
            raise incompatible("request routing identity")
        if (self.request_bytes is not None and owner.generation_token_counter is not self.counter
                or self.identity is not None and self.counter.identity != self.identity):
            raise incompatible("complete request identity")
        if endpoint == "/api/generate" and self.request_bytes is not None:
            try:
                matches = model_json(body).encode() == self.request_bytes
            except (ValueError, TypeError):
                matches = False
            if not matches:
                raise incompatible("complete request identity")
        if self.counter is not None:
            from pipeline.native_tokenizer.ollama import OllamaCounter
            if isinstance(self.counter, OllamaCounter):
                self.counter._check_binding()


class LocalModels:
    def __init__(self, base_url=None, chat_model=None, embedding_model=None, transport=None, *, preserve_content=False, generation_token_counter=None):
        self.base_url = base_url or os.getenv("OLLAMA_URL", "http://ollama:11434")
        if urlparse(self.base_url).hostname not in {"ollama", "localhost", "127.0.0.1", "::1", "host.docker.internal"}:
            raise ValueError("Local Ollama endpoint required")
        self.preserve_content = preserve_content
        self.local_only = True
        if preserve_content:
            parsed = urlparse(self.base_url)
            if (parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.scheme not in {"http", "https"}
                    or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
                raise ValueError("Preserved local content requires a loopback model endpoint.")
        self.chat_model = chat_model or os.getenv("LOGCHAT_CHAT_MODEL", "qwen2.5:1.5b")
        self.embedding_model = embedding_model or os.getenv("LOGCHAT_EMBEDDING_MODEL", "nomic-embed-text")
        self.transport = transport
        self._injected_token_counter = generation_token_counter
        self._token_session = ContextVar("local_model_token_session", default=None)
        self._request_binding = ContextVar("local_model_request_binding", default=None)
        self._generation_token_identity = getattr(generation_token_counter, "identity", None)

    @property
    def generation_token_counter(self):
        return (self._injected_token_counter if self._injected_token_counter is not None
                else self._token_session.get())

    @generation_token_counter.setter
    def generation_token_counter(self, value):
        self._injected_token_counter = value
        self._generation_token_identity = getattr(value, "identity", None)

    @asynccontextmanager
    async def generation_token_session(self, *, deadline=None):
        """Task-local plan ownership; injected adapters retain their lifecycle."""
        if self.generation_token_counter is not None:
            yield self.generation_token_counter
            return
        from pipeline.native_tokenizer.ollama import OllamaCounter
        now = asyncio.get_running_loop().time()
        counter = OllamaCounter(self, deadline=min(deadline if deadline is not None else now + 300, now + 300))
        token = self._token_session.set(counter)
        try:
            async with asyncio.timeout_at(counter.deadline):
                yield counter
        finally:
            try:
                await counter.aclose()
            finally:
                self._token_session.reset(token)

    @asynccontextmanager
    async def embedding_token_session(self, *, deadline=None):
        """Physical Nomic proof API only; does not change embedding recipes."""
        from pipeline.native_tokenizer.ollama import OllamaCounter
        now = asyncio.get_running_loop().time()
        counter = OllamaCounter(self, deadline=min(deadline if deadline is not None else now + 300, now + 300), embedding=True)
        try:
            async with asyncio.timeout_at(counter.deadline):
                yield counter
        finally:
            await counter.aclose()

    @contextmanager
    def _bind_requests(self, binding):
        token = self._request_binding.set(binding)
        try:
            binding.check(self)
            yield
        finally:
            self._request_binding.reset(token)

    @asynccontextmanager
    async def _response(self, client, method, endpoint, body):
        binding = self._request_binding.get()
        if binding is not None:
            binding.check(self, endpoint, body)
        # Build immutable URL and serialized content before the send can suspend.
        request = client.build_request(method,
            (binding.endpoint if binding is not None else self.base_url) + endpoint, json=body)
        response = await client.send(request, stream=True)
        try:
            if binding is not None:
                binding.check(self, endpoint, body)
            yield response
        finally:
            await response.aclose()

    async def installed(self):
        try:
            async with httpx.AsyncClient(trust_env=False, transport=self.transport, timeout=10) as client:
                data = await self._read_json(client, "GET", "/api/tags")
                names = [m["name"] for m in data.get("models", [])]

            def present(name):
                return name in names or name + ":latest" in names

            return {"chat": present(self.chat_model), "embedding": present(self.embedding_model),
                    "chat_model": self.chat_model, "embedding_model": self.embedding_model}
        except Exception:
            return {"chat": False, "embedding": False, "chat_model": self.chat_model, "embedding_model": self.embedding_model}

    async def _read_json(self, client, method, endpoint, *, body=None):
        # Bound the actual streamed bytes before JSON decoding; Content-Length
        # can be absent or incorrect. A model's num_predict is not a wire limit.
        async with self._response(client, method, endpoint, body) as response:
            if endpoint == "/api/generate" and response.status_code == 400:
                # Never expose provider content. Read only a bounded known JSON
                # error envelope; unrelated or malformed 400s stay generic.
                error_data = bytearray()
                complete = True
                async for part in response.aiter_bytes():
                    if len(error_data) + len(part) > MAX_CONTEXT_ERROR_BYTES:
                        complete = False
                        break
                    error_data.extend(part)
                try:
                    error_value = json.loads(error_data, object_pairs_hook=_unique_json_object)
                    message = error_value.get("error") if isinstance(error_value, dict) else None
                    if complete and isinstance(message, str) and message.startswith(NATIVE_CONTEXT_ERROR_PREFIX):
                        raise GenerationContextExceeded("Local model input exceeds its native context limit.")
                except (ValueError, TypeError):
                    pass
            response.raise_for_status()
            data = bytearray()
            async for part in response.aiter_bytes():
                if len(data) + len(part) > MAX_LOCAL_RESPONSE_BYTES:
                    raise ModelUnavailable("Local model response exceeded its byte budget.")
                data.extend(part)
        value = json.loads(data, object_pairs_hook=_unique_json_object)
        if not isinstance(value, dict):
            raise ModelUnavailable("Local model returned an invalid response object.")
        binding = self._request_binding.get()
        if binding is not None:
            binding.check(self, endpoint, body)
        return value

    async def _post(self, endpoint, body, timeout=180):
        from pipeline.native_tokenizer.ollama import CompatibilityError
        binding = self._request_binding.get()
        if binding is not None:
            binding.check(self, endpoint, body)
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    transport=binding.transport if binding is not None else self.transport, timeout=timeout) as client:
                return await self._read_json(client, "POST", endpoint, body=body)
        except CompatibilityError as error:
            raise ModelUnavailable(str(error)) from None
        except ModelUnavailable:
            raise
        except httpx.HTTPStatusError as error:
            raise ModelUnavailable(f"Local model request failed (HTTP {error.response.status_code}). Check the model configuration or retry after the local model recovers.") from None
        except httpx.TimeoutException:
            raise ModelUnavailable("Local model timed out. Use a shorter question or a smaller context, then retry.") from None
        except Exception:
            raise ModelUnavailable("Local model unavailable; run logchat models pull.") from None

    async def pull(self, model: str):
        if not model or len(model) > 200 or any(character.isspace() for character in model):
            raise ValueError("Choose a valid Ollama model name.")
        await self._post("/api/pull", {"model": model, "stream": False}, timeout=900)
        return {"ok": True, "model": model}

    async def _require_generation_context_guard(self):
        from pipeline.native_tokenizer.ollama import CompatibilityError
        binding = self._request_binding.get()
        if binding is not None:
            binding.check(self)
        # Recheck each generation so daemon restarts/model replacement cannot
        # inherit a stale capability cache. Metadata requests do not generate.
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    transport=binding.transport if binding is not None else self.transport, timeout=180) as client:
                version = await self._read_json(client, "GET", "/api/version")
                if version.get("version") not in VERIFIED_CONTEXT_GUARD_VERSIONS:
                    raise ModelUnavailable("Local generation requires a verified Ollama context guard (0.34.4).")
                model = await self._read_json(client, "POST", "/api/show", body={"model": self.chat_model})
                # Remote-backed models can proxy before the verified local
                # runner guard. Even partial/malformed routing must fail closed.
                for field in ("remote_host", "remote_model"):
                    if field in model and model[field] != "":
                        raise ModelUnavailable("Local generation requires a model without remote routing metadata.")
                details = model.get("details")
                capabilities = model.get("capabilities")
                # GGUF uses llama-server in the verified release. Stored chat
                # history can be trimmed before the completion-level guard;
                # admit only the current system and user messages we supply.
                if (not isinstance(details, dict) or details.get("format") != "gguf"
                        or not isinstance(capabilities, list) or "completion" not in capabilities
                        or model.get("messages", []) != []):
                    raise ModelUnavailable("Local generation backend does not have a verified lossless context guard.")
        except ModelUnavailable:
            raise
        except CompatibilityError as error:
            raise ModelUnavailable(str(error)) from None
        except Exception:
            raise ModelUnavailable("Local generation context guard could not be verified.") from None

    def generation_request(self, instruction, context, schema):
        body = generation_body(self.chat_model, instruction,
                               context if self.preserve_content else sanitize(context), schema)
        if len(body["prompt"]) > CONTEXT_CHARACTER_BUDGET:
            raise ModelUnavailable("Context exceeds the local model budget. Select fewer environments or a shorter question.")
        # Snapshot nested caller-owned schema before the first await.
        return json.loads(model_json(body))

    async def preflight_generation(self, instruction, context, schema):
        """Exact local counting only; never use generation as a sizing probe.

        The default preserved client lazily opens the reviewed local binding.
        A plan session reuses one owner; a standalone probe closes on return.
        """
        return await self._preflight_generation_body(self.generation_request(instruction, context, schema))

    async def _preflight_generation_body(self, body):
        from pipeline.token_budget import GenerationTokenCount, GenerationTokenIdentity
        counter = self.generation_token_counter
        if counter is None:
            if not self.preserve_content:
                raise ModelUnavailable("generation_token_capability_unavailable")
            async with self.generation_token_session():
                return await self._preflight_generation_body(body)
        from pipeline.native_tokenizer.ollama import OllamaCounter, CompatibilityError
        automatic = isinstance(counter, OllamaCounter)
        try:
            # Opening the lazy default also verifies its provider/model identity.
            proof = await counter.count(model_json(body).encode()) if automatic else None
            identity = counter.identity if automatic else self._generation_token_identity
            if (not isinstance(identity, GenerationTokenIdentity)
                    or identity.runtime_version not in VERIFIED_CONTEXT_GUARD_VERSIONS
                    or counter.identity != identity):
                raise ValueError()
            # Give the adapter immutable exact bytes, not mutable source objects.
            if not automatic:
                proof = await counter.count(model_json(body).encode())
            if not isinstance(proof, GenerationTokenCount) or counter.identity != identity:
                raise ValueError()
            return proof.fits(body, identity)
        except CompatibilityError as error:
            raise ModelUnavailable(str(error)) from None
        except TimeoutError:
            raise
        except Exception:
            if automatic:
                from pipeline.native_tokenizer.ollama import incompatible
                raise ModelUnavailable(str(incompatible("asset/runtime verification failed"))) from None
            raise ModelUnavailable("generation_token_proof_unavailable") from None

    async def generate(self, instruction, context, schema):
        if self.preserve_content and self.transport is None and self.generation_token_counter is None:
            async with self.generation_token_session():
                return await self.generate(instruction, context, schema)
        from pipeline.native_tokenizer.ollama import OllamaCounter, CompatibilityError
        counter = self.generation_token_counter
        binding = (counter._binding if isinstance(counter, OllamaCounter) else
                   _LocalRequestBinding(self.base_url, self.transport, self.chat_model))
        body = self.generation_request(instruction, context, schema)
        if not self.preserve_content and not isinstance(counter, OllamaCounter):
            return await self._generate(body, schema)
        binding = replace(binding, request_bytes=model_json(body).encode(), counter=counter)
        try:
            with self._bind_requests(binding):
                return await self._generate(body, schema)
        except CompatibilityError as error:
            raise ModelUnavailable(str(error)) from None

    async def _generate(self, body, schema):
        """Validate against the submitted snapshot; schema stays for QA compatibility."""
        await self._require_generation_context_guard()
        if self.generation_token_counter is not None:
            if not await self._preflight_generation_body(body):
                raise GenerationContextExceeded("Local model input exceeds its native context limit.")
        from pipeline.native_tokenizer.ollama import OllamaCounter, CompatibilityError
        counter = self.generation_token_counter
        binding = self._request_binding.get()
        if binding is not None:
            binding.check(self, "/api/generate", body)
            self._request_binding.set(replace(binding, identity=getattr(counter, "identity", None)))
        if isinstance(counter, OllamaCounter):
            try:
                await counter.verify_provider()
            except CompatibilityError as error:
                raise ModelUnavailable(str(error)) from None
            except Exception:
                from pipeline.native_tokenizer.ollama import incompatible
                raise ModelUnavailable(str(incompatible("provider verification failed"))) from None
        binding = self._request_binding.get()
        if binding is not None:
            binding.check(self, "/api/generate", body)
        data = await self._post("/api/generate", body)
        if binding is not None:
            binding.check(self, "/api/generate", body)
        if data.get("done_reason") == "length":
            raise ModelUnavailable("Local model exhausted its output budget.")
        try:
            value = json.loads(data["response"], object_pairs_hook=_unique_json_object)
            if not self.preserve_content:
                value = sanitize(value)
            return _validate_schema(value, body["format"])
        except (KeyError, ValueError, TypeError):
            raise ModelUnavailable("Local model returned invalid structured output.") from None

    async def summarize(self, events):
        secrets = (getattr(self, "api_key", None),)
        terms = observed_terms(events, secrets)
        # Durable facts use every event, independently of chat availability.
        return render_summary(events, terms, secrets)

    async def embed(self, text):
        data = await self._post("/api/embed", {"model": self.embedding_model,
            "input": redact_text(text)[:4000], "truncate": False})
        try:
            vector = data["embeddings"][0]
            if len(vector) != 768 or not all(isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x) for x in vector):
                raise ValueError()
            return vector
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelUnavailable("Embedding model must return 768 finite dimensions.") from None


class OpenAICompatibleModels(LocalModels):
    """OpenAI-compatible chat generation paired with local Ollama embeddings."""

    def __init__(self, api_base: str, chat_model: str, api_key: str | None = None, transport=None,
                 embedding_transport=None):
        super().__init__(transport=embedding_transport)
        self.api_base = validate_endpoint(api_base)
        if self.api_base.startswith("http://") and api_key:
            raise ValueError("API keys require HTTPS.")
        self.chat_model = chat_model
        self.api_key = api_key
        self.chat_transport = transport

    async def generate(self, instruction, context, schema):
        payload = json.dumps(sanitize(context, (self.api_key,)), ensure_ascii=False, default=str)
        if len(payload) > CONTEXT_CHARACTER_BUDGET:
            raise ModelUnavailable("Context exceeds the model budget. Select fewer environments or a shorter question.")
        if self.chat_transport is None:
            await ensure_safe_resolution(self.api_base)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        body = {
            "model": self.chat_model,
            "messages": [
                {"role": "system", "content": instruction + SYSTEM_SAFETY_SUFFIX},
                {"role": "user", "content": payload},
            ],
            "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {"name": "logchat_response", "strict": True, "schema": schema}},
        }
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=self.chat_transport, timeout=180) as client:
                response = await client.post(self.api_base + "/chat/completions", json=body, headers=headers)
                response.raise_for_status()
                data = response.json()
            if data["choices"][0].get("finish_reason") in {"length", "content_filter"}:
                raise ModelUnavailable("Configured chat model did not complete structured output.")
            content = data["choices"][0]["message"]["content"]
            value = content if isinstance(content, dict) else json.loads(content)
            return sanitize(_validate_schema(value, schema), (self.api_key,))
        except httpx.TimeoutException:
            raise ModelUnavailable("Configured chat model timed out. Check the endpoint or choose a smaller context.") from None
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise ModelUnavailable("Configured chat model is unavailable or returned invalid structured output.") from None


def _model_row(owner_id: str):
    """Read one owner's row through the API's authenticated RLS context."""
    from api.security import database
    with database(owner_id) as connection:
        return connection.execute(
            "SELECT provider,chat_model,base_url,api_key_ref FROM model_settings WHERE owner_id=%s", (owner_id,),
        ).fetchone()


def get_models(owner_id: str, *, transport=None, embedding_transport=None):
    """Build the query-time owner model; the builder intentionally remains local-only."""
    row = _model_row(owner_id)
    if not row or row["provider"] == "ollama":
        return LocalModels(chat_model=row["chat_model"] if row else None, transport=transport)
    try:
        credential = read_credential(
            str(row["api_key_ref"]) if row.get("api_key_ref") else None,
            owner_id=owner_id, purpose="model_api",endpoint=row['base_url'],
        )
    except RuntimeError:
        raise ModelUnavailable('The configured credential no longer matches this endpoint. Save the model settings again.') from None
    return OpenAICompatibleModels(
        row["base_url"], row["chat_model"], credential,
        transport=transport, embedding_transport=embedding_transport,
    )
