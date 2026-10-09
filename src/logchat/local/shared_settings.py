"""Explicit live selection of one local generation model for every native role.

Metadata follows Ollama's /api/tags and /api/show contracts. Capability metadata
is not a quality guarantee; selection also runs the installer's synthetic probe.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile

import httpx

from . import installer, rag_runtime
from .model_profile import ModelProfile
from logchat.rag.contracts import EmbeddingSpec
from logchat.rag.scheduler import SQLiteSchedulerStore
from logchat.rag.storage import SQLiteVectorStore

MAX_MODELS = 32
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SettingsError(RuntimeError):
    """Only fixed public messages belong here; never include provider responses."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def _existing(directory: Path):
    path = directory / rag_runtime.CONFIG_FILE
    if not path.exists():
        return None, None
    try:
        if path.stat().st_size > 32768:
            raise ValueError()
        original = path.read_bytes()
        value = json.loads(original)
        ModelProfile(value)
        installer.validate_endpoint(value["base_url"])
        spec = EmbeddingSpec(**value["embedding"])
        if spec.provider != "ollama":
            raise ValueError()
        return original, value
    except (ValueError, TypeError, KeyError, OSError, installer.InstallError):
        raise SettingsError(409, "The existing model profile cannot be safely reused. Repair it or choose a separate state directory.") from None


def _endpoint(value):
    try:
        return installer.validate_endpoint(value)
    except installer.InstallError:
        raise SettingsError(422, "Choose a loopback Ollama endpoint without credentials, paths or query parameters.") from None


def _model(value):
    try:
        return installer.validate_model(value)
    except installer.InstallError:
        raise SettingsError(422, "Choose one explicit installed generation model name without whitespace.") from None


async def _request(client, method, endpoint, *, payload=None):
    async with client.stream(method, endpoint, json=payload) as response:
        response.raise_for_status()
        data = bytearray()
        async for part in response.aiter_bytes():
            if len(data) + len(part) > MAX_RESPONSE_BYTES:
                raise ValueError("model_metadata_response_too_large")
            data.extend(part)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("model_metadata_invalid")
    return value


def _capabilities(name, value):
    # A loopback Ollama connection can itself refer to a hosted model.
    if (name.endswith(":cloud") or value.get("remote_host") or value.get("remote_model")
            or value.get("remote_url")):
        return None
    capabilities = value.get("capabilities")
    if (not isinstance(capabilities, list) or len(capabilities) > 32
            or any(not isinstance(item, str) or len(item) > 80 for item in capabilities)
            or "completion" not in capabilities):
        return None
    return sorted(set(capabilities))


def _selected(name, configured):
    return name == configured or (configured is not None and name == configured + ":latest")


async def list_models(store) -> dict:
    """Read only the configured loopback endpoint, or the documented local default."""
    _, existing = _existing(store.state_dir)
    endpoint = _endpoint(existing["base_url"] if existing else installer.DEFAULT_ENDPOINT)
    configured = ModelProfile(existing).generation_model if existing else None
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as client:
                tagged = await _request(client, "GET", endpoint + "/api/tags")
                installed = tagged.get("models")
                if not isinstance(installed, list):
                    raise ValueError()
                names = set()
                invalid = 0
                for row in installed:
                    try:
                        name = _model(row["name"])
                    except (KeyError, TypeError, SettingsError):
                        invalid += 1
                        continue
                    names.add(name)
                names = sorted(names, key=lambda name: (not _selected(name, configured), name))
                truncated = len(names) > MAX_MODELS
                semaphore = asyncio.Semaphore(4)
                async def candidate(name):
                    async with semaphore:
                        try:
                            detail = await _request(client, "POST", endpoint + "/api/show", payload={"model": name})
                            capabilities = _capabilities(name, detail)
                        except Exception:
                            return None
                        if capabilities is None:
                            return None
                        return {"name": name, "selected": _selected(name, configured), "capabilities": capabilities,
                                "generation_available": True, "readiness": "not_checked"}
                candidates = await asyncio.gather(*(candidate(name) for name in names[:MAX_MODELS]))
    except Exception:
        raise SettingsError(503, "Cannot list installed local models. Start Ollama and check the configured endpoint.") from None
    models = [item for item in candidates if item is not None]
    return {"provider": "ollama", "endpoint": endpoint, "configured_model": configured,
            "models": models, "omitted_models": len(names) + invalid - len(models), "truncated": truncated,
            "selection_requires_readiness_check": True,
            "notice": "Only verified local completion-capable models are listed. Selection checks structured output; model quality is not guaranteed."}


def _atomic_write(path: Path, data: bytes):
    fd, temporary = tempfile.mkstemp(prefix=".shared-model-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def select_model(store, endpoint: str | None, model: str, *, embedding_model: str | None = None, dimensions: int | None = None) -> dict:
    """Validate an explicit generation choice and atomically switch the shared profile.

    Existing evidence keeps its recorded model provenance. In-flight queries may
    finish with the profile they already obtained; original-event preparation is
    serialized against this change. Callers start a newly created runtime.
    """
    model = _model(model)
    if not rag_runtime._INTAKE_SLOT.acquire(blocking=False):
        raise SettingsError(409, "Log preparation or another model change is running. Retry after it finishes.")
    try:
        directory = Path(store.state_dir)
        original, existing = _existing(directory)
        endpoint = _endpoint(endpoint if endpoint is not None else
                             existing["base_url"] if existing else installer.DEFAULT_ENDPOINT)
        if existing:
            if (embedding_model not in (None, existing["embedding"]["model"]) or dimensions not in (None, existing["embedding"]["dimensions"])):
                raise SettingsError(422, "Keep the existing embedding index configuration; use a separate state directory to change it.")
            embedding_model = existing["embedding"]["model"]
            dimensions = existing["embedding"]["dimensions"]
        elif (not isinstance(embedding_model, str) or not embedding_model.strip() or type(dimensions) is not int or not 1 <= dimensions <= 4096):
            raise SettingsError(422, "Choose an embedding model and dimensions for initial setup; no default model is selected.")
        policy = existing.get("content_policy", "redacted_templates") if existing else "local_model_compact"
        try:
            async with asyncio.timeout(90):
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
                    detail = await _request(client, "POST", endpoint + "/api/show", payload={"model": model})
                if _capabilities(model, detail) is None:
                    raise SettingsError(422, "Choose an installed local completion-capable model. Hosted and embedding-only models are unsupported.")
                readiness = await installer.verify_generation(endpoint, model, embedding_model=embedding_model)
                # configure() validates actual embedding revision/dimensions and
                # SQLite support here, before the live file or profile changes.
                with tempfile.TemporaryDirectory(prefix=".model-check-", dir=directory) as staging:
                    staging = Path(staging)
                    if original is not None:
                        _atomic_write(staging / rag_runtime.CONFIG_FILE, original)
                    try:
                        configured = await rag_runtime.configure(staging, base_url=endpoint, model=embedding_model,
                            dimensions=dimensions, chat_model=model, content_policy=policy)
                    except ValueError:
                        raise SettingsError(409, "The embedding index must retain its model, dimensions and revision. The selected endpoint is incompatible; configuration was not changed.") from None
        except SettingsError:
            raise
        except Exception:
            raise SettingsError(503, "The selected model or embedding capability failed readiness. Configuration was not changed; check the model and retry.") from None
        if existing is not None and configured["embedding"] != existing["embedding"]:
            raise SettingsError(409, "The existing embedding index is incompatible. Configuration was not changed.")
        configured = {**(existing or {}), **configured}
        # A legacy equal-valued alias must not turn into a stage override.
        configured.pop("chunk_model", None)
        profile = ModelProfile(configured)
        spec = EmbeddingSpec(**configured["embedding"])
        public = {**profile.public(), "status": "configured", "shared_model": profile.generation_model,
                  "embedding_unchanged": existing is not None, "readiness": readiness,
                  "application": "new_log_preparation_and_future_relevance_checks"}
        runtime = store.rag_runtime
        created_runtime = runtime is None
        if runtime is None:
            runtime = rag_runtime.NativeRAGRuntime(store)
        backend, scheduler = runtime.backend, runtime.scheduler
        if backend is None:
            backend = SQLiteVectorStore(directory / "rag.db", spec)
            scheduler = SQLiteSchedulerStore(directory / "rag.db", connect=backend.connect)
        elif runtime.spec != spec:
            raise SettingsError(409, "The active embedding index is incompatible. Configuration was not changed.")
        destination = directory / rag_runtime.CONFIG_FILE
        with runtime._configuration_lock:
            current = destination.read_bytes() if destination.exists() else None
            if current != original:
                raise SettingsError(409, "Model configuration changed during validation. Retry with the current profile.")
            _atomic_write(destination, json.dumps(configured, indent=2).encode("utf-8"))
            # No await separates the commit and profile swap. All potentially
            # failing model/storage construction happened before this point.
            runtime.config, runtime.model_profile = configured, profile
            runtime.spec, runtime.backend, runtime.scheduler = spec, backend, scheduler
            runtime.preserve_content = profile.preserve_content
            runtime.provider = None
            runtime.state, runtime.error = "starting", None
            store.rag_runtime = runtime
        public["runtime_created"] = created_runtime
        return public
    except SettingsError:
        raise
    except Exception:
        raise SettingsError(503, "The model settings could not be saved. Existing model configuration was retained.") from None
    finally:
        rag_runtime._INTAKE_SLOT.release()
