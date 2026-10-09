"""FastAPI application for the portable, no-Docker local runtime."""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from connectors.local import MAX_PUSH_EVENTS, parse_push_events
from pipeline.models import LocalModels, ModelUnavailable, OpenAICompatibleModels, validate_endpoint
from pipeline.redaction import redact_text, sanitize
from pipeline.retrieval import QueryError, resolve_windows

from .store import LocalStore, utc_now

MAX_REQUEST_BYTES = 256 * 1024


class RequestLimitMiddleware:
    def __init__(self, app: ASGIApp, maximum: int = MAX_REQUEST_BYTES):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > self.maximum:
                await self._reject(send); return
        except ValueError:
            await self._reject(send); return
        consumed = 0

        async def bounded_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.maximum:
                    raise _BodyTooLarge()
            return message
        try:
            await self.app(scope, bounded_receive, send)
        except _BodyTooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body is too large."}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SharedModelInput(Input):
    endpoint: str = Field(default="http://127.0.0.1:11434", min_length=1,max_length=300)
    model: str = Field(min_length=1,max_length=160)


class CapturePolicyInput(Input):
    mode: str = Field(pattern="^(summary_only|retain_until_summarized)$")
    max_bytes: int = Field(default=100 * 1024 * 1024, ge=1024 * 1024, le=1024 * 1024 * 1024)
    retention_seconds: int = Field(default=86400, ge=60, le=30 * 86400)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=200)
    path: str | None = Field(default=None, max_length=1000)


class EnvironmentInput(Input):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


class FileCaptureInput(Input):
    path: str = Field(min_length=1,max_length=4000)
    from_start: bool = False


class ProviderCaptureInput(Input):
    kind: str = Field(pattern=r"^(docker|railway|vercel|cli)$")
    config: dict[str, Any] = Field(default_factory=dict)
    token: SecretStr | None = None


class SourceInput(Input):
    name: str = Field(min_length=1, max_length=200)
    environment: str = Field(default="dev", min_length=1, max_length=100)
    port: int | None = Field(default=None, ge=1, le=65535)
    kind: str = Field(default="process", pattern=r"^(process|push|docker|railway|vercel|cli)$")


class EventInput(Input):
    source_id: UUID
    events: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_PUSH_EVENTS)


class Question(Input):
    question: str = Field(min_length=1, max_length=2000)
    environment_ids: list[UUID] = Field(min_length=1, max_length=4)
    timezone: str = Field(default="America/New_York", max_length=100)
    start: datetime | None = None
    end: datetime | None = None
    compare_start: datetime | None = None
    compare_end: datetime | None = None
    service: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def complete_windows(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Start and end must be supplied together.")
        if (self.compare_start is None) != (self.compare_end is None):
            raise ValueError("Both comparison timestamps are required.")
        return self


class ConversationInput(Input):
    title: str = Field(default="New investigation", min_length=1, max_length=200)


class Turn(Input):
    question: str = Field(min_length=1, max_length=2000)
    request_id: UUID = Field(default_factory=uuid4)
    environment_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=4)
    timezone: str | None = Field(default=None, max_length=100)
    start: datetime | None = None
    end: datetime | None = None
    compare_start: datetime | None = None
    compare_end: datetime | None = None
    service: str | None = Field(default=None, max_length=200)


class UIPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environments_enabled: bool


class ModelInput(Input):
    provider: str = Field(pattern=r"^(extractive|ollama|openai_compatible)$")
    base_url: str | None = Field(default=None, max_length=1000)
    chat_model: str | None = Field(default=None, max_length=200)
    embedding_model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4000)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else None


def _public_message(row: Any) -> dict[str, Any]:
    value = dict(row)
    result = value.get("result")
    return {"id": value["id"], "request_id": value["request_id"], "role": value["role"],
            "content": value["content"], "result": json.loads(result) if isinstance(result, str) else result,
            "created_at": value["created_at"]}


def _allowed_host(value: str, port: int) -> bool:
    try:
        parsed = urlparse("//" + value)
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"} and (parsed.port is None or parsed.port == port)
    except ValueError:
        return False


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _window(start: datetime | None, end: datetime | None) -> tuple[str, str]:
    finish = end or datetime.now(timezone.utc)
    begin = start or finish - timedelta(hours=24)
    if begin.tzinfo is None: begin = begin.replace(tzinfo=timezone.utc)
    if finish.tzinfo is None: finish = finish.replace(tzinfo=timezone.utc)
    if begin >= finish:
        raise HTTPException(422, "The time window must start before it ends.")
    return _iso(begin), _iso(finish)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    events = sum(row["event_count"] for row in rows)
    errors = 0
    duration_count = sum(row["duration_count"] for row in rows)
    duration_sum = sum(row["duration_sum_ms"] for row in rows)
    mins = [row["duration_min_ms"] for row in rows if row["duration_min_ms"] is not None]
    maxes = [row["duration_max_ms"] for row in rows if row["duration_max_ms"] is not None]
    for row in rows:
        if row["level"] in {"error", "fatal", "critical"}:
            errors += row["event_count"]
        else:
            errors += sum(count for status, count in row["status_counts"].items() if str(status).startswith("5"))
    return {"events": events, "errors": errors, "duration_count": duration_count,
            "duration_mean_ms": round(duration_sum / duration_count, 3) if duration_count else None,
            "duration_min_ms": min(mins) if mins else None, "duration_max_ms": max(maxes) if maxes else None}


def _answer(store: LocalStore, project_id: str, body: Question, *, connection=None,
            previous_questions: list[str] | None = None) -> dict[str, Any]:
    normalized = re.sub(r"\blast(\d+)(hours?|days?)\b", r"last \1 \2", body.question, flags=re.I)
    resolved_body = body.model_copy(update={"question": normalized})
    try:
        resolved, assumptions = resolve_windows(resolved_body)
    except QueryError as error:
        raise HTTPException(422, str(error)) from None
    environment_ids = [str(value) for value in body.environment_ids]
    def load_environments(conn):
        existing = conn.execute(
            "SELECT id,name FROM environments WHERE project_id=? AND id IN (" + ",".join("?" for _ in environment_ids) + ")",
            (project_id, *environment_ids),
        ).fetchall()
        if len(existing) != len(set(environment_ids)):
            raise HTTPException(404, "Environment not found.")
        return {row["id"]: row["name"] for row in existing}
    if connection is None:
        with store.connection() as scoped:
            environments = load_environments(scoped)
    else:
        environments = load_environments(connection)
    periods = [(item["label"], _iso(item["start"]), _iso(item["end"])) for item in resolved]
    clues = [redact_text(value)[:400] for value in (previous_questions or [])][:4]
    retrieval_query = body.question + (" " + " ".join(clues) if clues else "")
    all_rows: dict[str, dict[str, Any]] = {}
    cells, gaps, statements, findings = [], [], [], []
    for label, start, end in periods:
        for environment_id in environment_ids:
            rows = store.evidence(project_id, retrieval_query, environment_ids=[environment_id], start=start,
                                  end=end, service=body.service, limit=20, connection=connection)
            for row in rows: all_rows[row["id"]] = row
            metric = _metrics(rows)
            name = environments[environment_id]
            cell = {"id": f"{environment_id}:{label}", "environment": name,
                    "window": {"label": label, "start": start, "end": end}, "metrics": metric,
                    "evidence_ids": [row["id"] for row in rows]}
            cell["coverage"] = {"complete": False, "observed_events": metric["events"],
                                "measured_events": metric["duration_count"],
                                "observed_mean_duration_ms": metric["duration_mean_ms"],
                                "notes": "Push delivery proves observations only; continuous capture is not known."}
            cells.append(cell)
            gaps.append(f"{name} / {label}: observations are available, but continuous capture is not established.")
            partial = store.partial_bucket_count(project_id, environment_id, start, end,
                                                  service=body.service, connection=connection)
            if partial:
                gaps.append(f"{name} / {label}: {partial} boundary bucket(s) were excluded because the requested window only partially overlaps them.")
            if not rows:
                gaps.append(f"{name} / {label}: no matching summarized evidence; behavior is unknown.")
                statements.append(f"{name} / {label}: no matching summarized evidence.")
            else:
                citations = " ".join(f"[{row['id']}]" for row in rows)
                duration = (f" Mean duration {metric['duration_mean_ms']} ms across {metric['duration_count']} measured events"
                            f" (min {metric['duration_min_ms']} ms, max {metric['duration_max_ms']} ms).") if metric["duration_count"] else " No duration measurements were present."
                observation = f"{metric['events']} observed events, including {metric['errors']} error events.{duration}"
                statements.append(f"{name} / {label}: {observation} {citations}")
                findings.append({
                    "observation": observation,
                    "interpretation": "These retained matching aggregates support observed counts and measurements only; they do not establish complete traffic totals or a cause.",
                    "evidence_ids": cell["evidence_ids"].copy(),
                    "scope": {"project_id": project_id, "environment_id": environment_id,
                              "environment": name, "service": body.service,
                              "window": cell["window"].copy(), "timezone": body.timezone},
                    "provenance": "deterministic_aggregate",
                })
    ordered = sorted(all_rows.values(), key=lambda item: item["bucket_start"], reverse=True)
    cited = [row["id"] for row in ordered]
    start_labels = "; ".join(f"{label} {start} to {end}" for label, start, end in periods)
    scope = ", ".join(environments[value] for value in environment_ids)
    answer = redact_text(f"Scope: {scope}; {start_labels}; timezone {body.timezone}. " + " ".join(statements))
    return {"answer": answer, "evidence": ordered, "cited_evidence_ids": cited, "gaps": gaps,
            "findings": findings,
            "provenance": {"provider": "extractive", "model": "deterministic_aggregate",
                           "status": "extractive", "reason": "default"},
            "plan": {"project_id": project_id, "timezone": body.timezone, "cells": cells, "assumptions": assumptions + [
                "Counts cover retained summarized events in the explicit windows.",
                "No matching evidence is treated as unknown, not as proof that no event occurred.",
            ]}}


async def _optional_model_answer(store: LocalStore, result: dict[str, Any], question: str,
                                 previous_questions: list[str] | None = None, conversational_context: dict | None = None) -> dict[str, Any]:
    settings = store.settings()
    # Configured provider is intent, not evidence of which provider answered.
    result["provenance"] = {"provider": "extractive", "model": "deterministic_aggregate",
                            "status": "extractive", "reason": "default"}
    if settings["provider"] == "extractive" or not result["evidence"]:
        if not result["evidence"]:
            result["provenance"]["reason"] = "empty_evidence"
        return result
    if settings["provider"] == "ollama":
        models = LocalModels(base_url=settings["base_url"] or "http://127.0.0.1:11434",
                             chat_model=settings["chat_model"], embedding_model=settings["embedding_model"])
    else:
        models = OpenAICompatibleModels(settings["base_url"], settings["chat_model"], store.model_api_key())
    known = [row["id"] for row in result["evidence"]]
    # Keep all computed evidence in the response, but bound prose-model detail.
    # Repeated patterns or multiple environments must not overflow LocalModels'
    # 24k context budget. Each populated environment/period gets representation.
    selected = []
    for cell in result['plan'].get('cells', []):
        ids = set(cell.get('evidence_ids', []))
        cell_rows = [row for row in result['evidence'] if row['id'] in ids][:2]
        selected.extend(row for row in cell_rows if row['id'] not in {item['id'] for item in selected})
    if not selected:
        selected = result['evidence'][:8]
    selected_ids = {row['id'] for row in selected}
    model_plan = {**result['plan'], 'cells': [{**cell,
        'evidence_ids': [identifier for identifier in cell.get('evidence_ids', []) if identifier in selected_ids],
        'retained_evidence_count': len(cell.get('evidence_ids', []))}
        for cell in result['plan'].get('cells', [])]}
    schema = {"type": "object", "properties": {
        "answer": {"type": "string"},
        "cited_evidence_ids": {"type": "array", "maxItems": len(known), "uniqueItems": True,
                               "items": {"type": "string", "enum": known}},
    }, "required": ["answer", "cited_evidence_ids"], "additionalProperties": False}
    context = {"question": redact_text(question),
               "conversational_memory_not_evidence": conversational_context or {},
               "previous_questions": [redact_text(value)[:400] for value in (previous_questions or [])][:4],
               "plan": model_plan, "gaps": result["gaps"],
               "context_selection": "Up to two detail rows per cell. Computed metrics cover the full retrieved set, which can itself be limited; they are not complete traffic totals.",
               "computed_answer": result["answer"],
               "evidence": [{key: row[key] for key in ("id", "environment", "source", "service", "level",
                            "summary", "event_count", "bucket_start", "bucket_end", "duration_count",
                            "duration_sum_ms", "duration_min_ms", "duration_max_ms", "status_counts")}
                            for row in selected]}
    try:
        computed_answer = result["answer"]
        computed_citations = list(result.get("cited_evidence_ids") or known)
        # Hard character budget, independent of model/provider tokenization.
        if len(json.dumps(context)) > 24000:
            context["computed_answer"] = "See scoped metrics in plan; full deterministic answer retained outside model input."
        while len(json.dumps(context)) > 24000 and context["evidence"]:
            context["evidence"].pop()
        if len(json.dumps(context)) > 24000:
            raise ModelUnavailable("Model input exceeds bounded budget.")
        generated = await models.generate(
            "Select the evidence IDs most relevant to the current question from the supplied redacted aggregates. Return a short selection description and cited_evidence_ids. Only the selection will be used; free explanations are not published. Conversation memory clarifies references and constraints but is never log evidence. Preserve explicit scope. Do not infer causes or complete traffic totals.",
            context, schema,
        )
        citations = generated["cited_evidence_ids"]
        answer = redact_text(generated["answer"]).strip()
        if not answer or not citations:
            raise ModelUnavailable("Model did not cite evidence.")
        if any(value not in known for value in citations):
            raise ModelUnavailable("Model cited unknown evidence.")
        inline = re.findall(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", answer)
        if any(value not in known for value in inline):
            raise ModelUnavailable("Model included an unknown evidence reference.")
        model_citations = list(dict.fromkeys([*citations, *inline]))
        # The computed section can reference evidence the prose model did not
        # select.  Keep that full evidence/citation contract intact.
        # Free model prose cannot be verified for entailment by citation validation.
        # Use its valid evidence selection, then render retained observations ourselves.
        observations = [f"{row['summary']} [{row['id']}]" for row in selected if row['id'] in model_citations]
        result["answer"] = "Selected retained observations: " + "\n".join(observations) + "\n\nComputed scope and metrics: " + computed_answer
        result["model_output_policy"] = "evidence_selection_only; unsupported generated prose is not published"
        result["cited_evidence_ids"] = list(dict.fromkeys([*computed_citations, *model_citations]))
        result["provenance"] = {"provider": settings["provider"], "model": getattr(models, "chat_model", settings["chat_model"]),
                                "status": "model", "computed_findings_provider": "extractive"}
    except (ModelUnavailable, ValueError, TypeError, KeyError):
        result["provenance"].update(status="fallback", reason="model_unavailable_or_invalid")
        result["gaps"].append("The configured optional model was unavailable; the deterministic extractive answer was used.")
    return result


def create_app(state_dir: str | Path, port: int, *, capture_host: bool = True) -> FastAPI:
    """Create a loopback-only app.  The lifecycle binds it to 127.0.0.1."""
    store = LocalStore(state_dir)
    from .telemetry import HostCapture
    host = HostCapture(store)
    from .files import FileCapture
    files = FileCapture(store)
    from .providers import ProviderCollection
    providers = ProviderCollection(store)
    @asynccontextmanager
    async def lifespan(application):
        if capture_host and os.getenv("LOGCHAT_CAPTURE_HOST", "1") != "0": host.start()
        if store.rag_runtime is not None: await store.rag_runtime.start()
        await files.start()
        await providers.start()
        try: yield
        finally:
            await providers.stop()
            await files.stop()
            if store.rag_runtime is not None: await store.rag_runtime.stop()
            host.stop()
    app = FastAPI(title="logchat local", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.host_capture = host
    app.state.file_capture = files
    app.state.provider_capture = providers
    app.state.store, app.state.port = store, port
    app.add_middleware(RequestLimitMiddleware)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        host = request.headers.get("host", "")
        if not _allowed_host(host, port):
            return Response('{"detail":"Local host required."}', status_code=400, media_type="application/json")
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlparse(origin)
                origin_host = parsed.netloc
                if parsed.scheme != request.url.scheme or not _allowed_host(origin_host, port) or origin_host.lower() != host.lower():
                    raise ValueError()
            except ValueError:
                return Response('{"detail":"Foreign origin rejected."}', status_code=403, media_type="application/json")
        return await call_next(request)

    def control(request: Request) -> None:
        supplied = _bearer(request) or request.cookies.get("local_session")
        if not supplied or not secrets.compare_digest(supplied, store.control_token):
            raise HTTPException(401, "Local control session required.")

    @app.get("/health/live")
    @app.get("/health/ready")
    def health():
        return {"status": "ready", "mode": "local"}

    static_dir = Path(__file__).parent / "static"
    app.mount("/local-assets", StaticFiles(directory=static_dir, check_dir=False), name="local-assets")

    @app.get("/", response_class=HTMLResponse)
    def index(response: Response):
        index_path = static_dir / "index.html"
        content = index_path.read_text(encoding="utf-8") if index_path.exists() else "<!doctype html><title>logchat</title><main>logchat local</main>"
        response.set_cookie("local_session", store.control_token, httponly=True, samesite="strict", secure=False, path="/")
        return HTMLResponse(content=content, headers=response.headers)

    @app.get("/host/status", dependencies=[Depends(control)])
    def host_status():
        return host.status()

    @app.get("/host/timeline", dependencies=[Depends(control)])
    def host_timeline(category: str = Query(default="", pattern="^(|system|network|gpu|process|application)$"),
                      limit: int = Query(default=100, ge=1, le=200), project_id: UUID | None = None,
                      start: datetime | None = None, end: datetime | None = None):
        if (start is None) != (end is None):
            raise HTTPException(422, "Supply start and end together.")
        if start and end:
            if start.tzinfo is None or end.tzinfo is None or start >= end:
                raise HTTPException(422, "Use a complete increasing window with timezone offsets.")
            start = start.astimezone(timezone.utc)
            end = end.astimezone(timezone.utc)
        events = host.events(category=category, limit=limit, start=_iso(start) if start else None, end=_iso(end) if end else None) if category != "application" else []
        if project_id and category in {"", "application"}:
            for row in store.evidence(str(project_id), "", start=_iso(start) if start else None, end=_iso(end) if end else None, limit=limit):
                events.append({"id": row["id"], "timestamp": row["bucket_end"], "category": "application",
                    "level": row["level"], "summary": row["summary"], "process": row["service"],
                    "project_id": str(project_id), "metrics": {"events": row["event_count"]},
                    "bucket_start": row["bucket_start"], "bucket_end": row["bucket_end"]})
        events.sort(key=lambda row: row["timestamp"], reverse=True)
        return {"events": events[:limit], "capture": host.status(),
                "scope": "Accessible machine observations plus selected-project aggregate evidence; OS-user boundary.",
                "limitations": ["Network telemetry is counters and accessible connection metadata, not HTTP request bodies or packet capture.",
                    "Existing applications' stdout is unavailable unless connected through the wrapper, SDK or a readable log source.",
                    "OS permissions and GPU drivers determine availability. Host observations do not establish application causation.",
                    "Application rows represent aggregate windows; host timestamps represent sampled observations."]}

    @app.get("/projects", dependencies=[Depends(control)])
    def projects(): return store.list_projects()

    @app.post("/projects", status_code=201, dependencies=[Depends(control)])
    def create_project(body: ProjectInput): return store.create_project(body.name, body.path)

    @app.get("/projects/{project_id}/environments", dependencies=[Depends(control)])
    def environments(project_id: UUID): return store.list_environments(str(project_id))

    @app.post("/projects/{project_id}/environments", status_code=201, dependencies=[Depends(control)])
    def create_environment(project_id: UUID, body: EnvironmentInput): return store.create_environment(str(project_id), body.name)

    @app.get("/projects/{project_id}/sources", dependencies=[Depends(control)])
    def sources(project_id: UUID): return store.list_sources(str(project_id))

    @app.post("/projects/{project_id}/sources", status_code=201, dependencies=[Depends(control)])
    def create_source(project_id: UUID, body: SourceInput):
        return store.create_source(str(project_id), body.name, body.environment, body.kind, body.port)

    @app.post("/projects/{project_id}/sources/{source_id}/file",dependencies=[Depends(control)])
    def connect_file(project_id: UUID, source_id: UUID, body: FileCaptureInput):
        try:
            return files.configure(str(project_id),str(source_id),body.path,from_start=body.from_start)
        except (OSError,ValueError):
            raise HTTPException(422,"Select an accessible regular local file and a source in this project; semantic memory is required.") from None

    @app.delete("/projects/{project_id}/sources/{source_id}/file",dependencies=[Depends(control)])
    def disconnect_file(project_id: UUID, source_id: UUID):
        files.disable(str(project_id),str(source_id))
        return {"capture":"stopped","application_stopped":False}

    @app.post("/projects/{project_id}/sources/{source_id}/provider", dependencies=[Depends(control)])
    def connect_provider(project_id: UUID, source_id: UUID, body: ProviderCaptureInput):
        from cli.secrets import write_credential, delete_credential
        reference = None
        try:
            config = dict(body.config)
            if body.token is not None:
                if body.kind not in {"railway", "vercel"} or config.get("token_env") or config.get("token_ref"):
                    raise ValueError()
                token = body.token.get_secret_value()
                if not 1 <= len(token) <= 4096 or any(ord(c) < 33 for c in token):
                    raise ValueError()
                reference = write_credential(token, purpose="native_provider",
                    project_id=str(project_id), source_id=str(source_id), kind=body.kind)
                config["token_ref"] = reference
            return providers.configure(str(project_id), str(source_id), body.kind, config)
        except (OSError, ValueError, RuntimeError):
            if reference is not None: delete_credential(reference)
            raise HTTPException(422, "Provider configuration rejected. Check source ownership, adapter fields and credentials; memory and cursors are retained.") from None

    @app.delete("/projects/{project_id}/sources/{source_id}/provider", dependencies=[Depends(control)])
    def disconnect_provider(project_id: UUID, source_id: UUID):
        try:
            providers.disable(str(project_id), str(source_id))
            return {"capture": "stopped", "application_stopped": False}
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(422, "Provider source could not be disconnected.") from None

    @app.post("/projects/{project_id}/providers/poll", dependencies=[Depends(control)])
    async def poll_providers(project_id: UUID):
        store.list_sources(str(project_id))  # Check project before selecting sources.
        results = []
        for source in providers.status(str(project_id)):
            results.extend(await providers.poll_once(source["source_id"]))
        return results

    @app.get("/providers", dependencies=[Depends(control)])
    def provider_status():
        return providers.status()

    @app.post("/projects/{project_id}/events")
    def push_events(project_id: UUID, body: EventInput, request: Request):
        token = _bearer(request)
        if not token or not store.authenticate_source(str(project_id), str(body.source_id), token):
            raise HTTPException(401, "Source token is invalid for this project and source.")
        try:
            events = parse_push_events(body.events, source=str(body.source_id),
                preserve_fields=store.rag_runtime is not None and store.rag_runtime.preserve_content)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        try:
            return store.ingest(str(project_id), str(body.source_id), events)
        except ValueError:
            raise HTTPException(409, "Semantic preparation or replay was rejected; no successful processing cursor advanced.") from None
        except ModelUnavailable as error:
            from .raw_capture import processing_failure
            category, phase = processing_failure(error)
            raise HTTPException(503, "Local summarization is unavailable; originals were not stored. Retry the same events.",
                                headers={"X-Logchat-Processing-Category": category,
                                         "X-Logchat-Processing-Phase": phase}) from None

    @app.get("/projects/{project_id}/status", dependencies=[Depends(control)])
    def status(project_id: UUID):
        result = store.status(str(project_id))
        result["file_capture"] = files.status(str(project_id))
        result["provider_capture"] = providers.status(str(project_id))
        if store.rag_runtime is not None:
            result["rag"] = store.rag_runtime.status(str(project_id))
        return result

    @app.post("/projects/{project_id}/retry", dependencies=[Depends(control)])
    def retry_semantic(project_id: UUID):
        sources = store.list_sources(str(project_id))
        if store.rag_runtime is None:
            raise HTTPException(409,"Semantic memory is not configured for this instance.")
        if store.rag_runtime.scheduler is None:
            raise HTTPException(409,"Configure a model before retrying semantic work.")
        retried = sum(store.rag_runtime.scheduler.retry_failed(
            store.rag_runtime.identity(str(project_id),source["id"]),limit=32) for source in sources[:64])
        return {"retried":retried,"sources_checked":min(len(sources),64),
                "sources_omitted":max(0,len(sources)-64),"attempts_remain_bounded":True}

    @app.post("/projects/{project_id}/ask", dependencies=[Depends(control)])
    async def ask(project_id: UUID, body: Question):
        if store.rag_runtime is not None:
            try:
                return await store.rag_runtime.context(str(project_id),body)
            except ModelUnavailable:
                raise HTTPException(503, "Semantic model unavailable; saved memory and pending work are retained.") from None
            except (ValueError,QueryError):
                raise HTTPException(422, "Invalid semantic query scope or time window.") from None
        result = _answer(store, str(project_id), body)
        return await _optional_model_answer(store, result, body.question)

    @app.get("/projects/{project_id}/search", dependencies=[Depends(control)])
    async def search(project_id: UUID, q: str = Query(default="", max_length=200), environment_id: UUID | None = None):
        if store.rag_runtime is not None:
            environments = store.list_environments(str(project_id))
            valid = {item["id"] for item in environments}
            if environment_id is not None and str(environment_id) not in valid:
                raise HTTPException(422,"Environment is not part of this project.")
            if q.strip():
                ids = [environment_id] if environment_id else [UUID(item["id"]) for item in environments]
                if len(ids)>4:
                    raise HTTPException(422,"Select an environment when a project has more than four environments.")
                return await ask(project_id,Question(question=q,environment_ids=ids))
            if store.rag_runtime.backend is None:
                return {"evidence":[], "retrieval":"unavailable_model_not_configured", "pending_not_searchable":True}
            from dataclasses import asdict
            chunks = store.rag_runtime.backend.list_chunks(owner_id="local-os-user",project_id=str(project_id),
                environment_id=str(environment_id) if environment_id else None)
            rows = json.loads(json.dumps([asdict(chunk) for chunk in chunks],default=lambda value:value.isoformat()))
            return {"evidence":rows,"retrieval":"memory_browse","limit":100,"limit_reached":len(rows)==100}
        rows = store.evidence(str(project_id), q, environment_ids=[str(environment_id)] if environment_id else None)
        return {"evidence": rows}

    @app.get("/projects/{project_id}/memories/{memory_id}", dependencies=[Depends(control)])
    def memory(project_id: UUID, memory_id: str):
        semantic_id = bool(re.fullmatch(r"[a-f0-9]{64}",memory_id))
        if not semantic_id:
            try:
                memory_id = str(UUID(memory_id))
            except ValueError:
                raise HTTPException(422,"Invalid supporting memory identifier.") from None
            return store.memory(str(project_id),memory_id)
        if store.rag_runtime is not None:
            from dataclasses import asdict
            if store.rag_runtime.backend is None:
                raise HTTPException(404,"No semantic memories exist until a model is configured.")
            chunk = store.rag_runtime.backend.get_chunk(memory_id, owner_id="local-os-user", project_id=str(project_id))
            if chunk is None:
                raise HTTPException(404, "Supporting memory not found in this project.")
            result = asdict(chunk)
            if chunk.compression_version == "local-model-compact-v1":
                from logchat.rag.sections import validate_compact_chunk, compact_records_for_chunk
                compact = validate_compact_chunk(chunk)
                result.update(important_fields=compact["important_fields"], uncertainties=compact["uncertainties"],
                    retained_events=list(compact_records_for_chunk(chunk)), original_records_retained=False,
                    content_role="unverified_model_summary_with_preserved_fields")
            if chunk.compression_version in {"local-model-section-v1", "local-model-summary-v1"}:
                from logchat.rag.sections import records_for_chunk
                result["records"] = list(records_for_chunk(chunk))
            result = json.loads(json.dumps(result, default=lambda value: value.isoformat()))
            result.update(id=chunk.chunk_id,project_id=chunk.identity.project_id,
                environment_id=chunk.identity.environment_id,source_id=chunk.identity.source_id,
                bucket_start=chunk.window.start.isoformat(),bucket_end=chunk.window.end.isoformat(),
                **asdict(chunk.metrics))
            return {"memory":result,"provenance":{"snapshot":False,"immutable":True,
                "retrieval":"semantic_hybrid","embedding":asdict(store.rag_runtime.spec)}}
        return store.memory(str(project_id), str(memory_id))

    @app.get("/projects/{project_id}/coverage", dependencies=[Depends(control)])
    def coverage(project_id: UUID): return {"coverage": store.coverage(str(project_id))}

    @app.get("/projects/{project_id}/integration", dependencies=[Depends(control)])
    def integration(project_id: UUID):
        project_id_s = str(project_id)
        sources = store.list_sources(project_id_s)
        project = next((item for item in store.list_projects() if item["id"] == project_id_s), None)
        if not project:
            raise HTTPException(404, "Project not found.")
        path = project.get("path") or "PROJECT_PATH"
        shell_path = shlex.quote(path)
        process = next((item for item in sources if item["kind"] == "process"), None)
        app_port = process.get("port") if process else None
        attach = f"logchat local attach --project {shell_path}" + (f" --port {app_port}" if app_port else " --port APP_PORT")
        return {"project_id": project_id_s, "sources": sources, "commands": {
                    "attach": attach,
                    "capture_next_run": f"logchat local run --project {shell_path} -- npm run dev",
                    "structured_push_or_agent": f"logchat local integration --project {shell_path} --instructions",
                },
                "instructions": "Attaching records process metadata only. Capture stdout by starting the next development run through `logchat local run`, or use the protected structured-push integration. Source and control credentials stay in the local credential store and are never placed in these commands."}

    @app.post("/projects/{project_id}/conversations", status_code=201, dependencies=[Depends(control)])
    def create_conversation(project_id: UUID, body: ConversationInput):
        return store.create_conversation(str(project_id), body.title)

    @app.get("/projects/{project_id}/conversations", dependencies=[Depends(control)])
    def conversations(project_id: UUID): return store.list_conversations(str(project_id))

    def get_conversation(conversation_id: UUID, project_id: UUID | None = None):
        return store.conversation(str(conversation_id), str(project_id) if project_id else None)

    app.get("/conversations/{conversation_id}", dependencies=[Depends(control)])(get_conversation)

    @app.get("/projects/{project_id}/conversations/{conversation_id}", dependencies=[Depends(control)])
    def project_conversation(project_id: UUID, conversation_id: UUID):
        return get_conversation(conversation_id, project_id)

    async def conversation_turn(conversation_id: UUID, body: Turn, project_id: UUID | None = None):
        conversation_id_s, request_id = str(conversation_id), str(body.request_id)
        preserve = store.rag_runtime is not None and store.rag_runtime.preserve_content
        request_value = body.model_dump(mode="json", exclude={"request_id"})
        if not preserve:
            request_value = sanitize(request_value)
        with store.connection() as connection:
            conversation = store.conversation(conversation_id_s, str(project_id) if project_id else None, connection)["conversation"]
            project = conversation["project_id"]
            previous = connection.execute("""SELECT role,content,request,result FROM conversation_messages
                WHERE conversation_id=? AND role='user' ORDER BY rowid DESC LIMIT 12""", (conversation_id_s,)).fetchall()
            exact = connection.execute("SELECT role,content,result,request,id,created_at,request_id FROM conversation_messages WHERE conversation_id=? AND request_id=?",
                                       (conversation_id_s, request_id)).fetchall()
            if exact:
                user = next((row for row in exact if row["role"] == "user"), None)
                saved_request = json.loads(user["request"]) if user and user["request"] else None
                saved_input = saved_request.get("_input", saved_request) if isinstance(saved_request, dict) else saved_request
                if saved_input != request_value:
                    raise HTTPException(409, "This request ID belongs to a different question.")
                assistant = next((row for row in exact if row["role"] == "assistant"), None)
                if assistant:
                    return {"user_message": _public_message(user), "assistant_message": _public_message(assistant),
                            "result": json.loads(assistant["result"])}
            prior_requests = []
            for row in previous:
                if row["role"] == "user" and row["request"]:
                    value = json.loads(row["request"])
                    prior_requests.append(value.get("_effective", value))
            if body.environment_ids is None and not prior_requests:
                raise HTTPException(422, "Choose at least one environment for the first question.")
            values = request_value.copy()
            temporal = bool(re.search(r"\btoday\b|\byesterday\b|\b\d+\s*-?\s*(?:minutes?|hours?|days?)\b|\b(?:last|past|this) hour\b|\b(?:last|this) week\b|\blast month\b", body.question, re.I))
            for key in ("environment_ids", "timezone", "start", "end", "compare_start", "compare_end", "service"):
                if (temporal and key in {"start", "end", "compare_start", "compare_end"}) or (body.start is not None and body.compare_start is None and key in {"compare_start", "compare_end"}):
                    continue
                if values.get(key) is None:
                    values[key] = next((prior.get(key) for prior in prior_requests if prior.get(key) is not None), None)
            values["environment_ids"] = values.get("environment_ids") or []
            values["timezone"] = values.get("timezone") or "America/New_York"
            question = Question.model_validate(values)
            from .context import read
            memory = read(connection, conversation_id_s, body.question)
            previous_questions = memory["recalled"]
        user_context = {"constraints": memory["constraints"], "recent": memory["recent"], "recalled": memory["recalled"], "metadata": memory["metadata"]}
        if store.rag_runtime is not None:
            try:
                result = await store.rag_runtime.context(project,question,user_context=user_context)
            except ModelUnavailable:
                raise HTTPException(503,"Semantic model unavailable; saved memory and pending work are retained.") from None
            except (ValueError,QueryError):
                raise HTTPException(422,"Invalid semantic query scope or time window.") from None
        else:
            result = sanitize(_answer(store, project, question, previous_questions=previous_questions))
            result = await _optional_model_answer(store, result, body.question, previous_questions, user_context)
        result["context_metadata"] = memory["metadata"]
        # Persist the resolved windows so later follow-ups keep the same evidence scope.
        resolved, _ = resolve_windows(question,max_days=366 if store.rag_runtime is not None else 90)
        effective = question.model_dump(mode="json")
        current = next(item for item in resolved if item["label"] == "current")
        effective.update(start=_iso(current["start"]), end=_iso(current["end"]))
        prior = next((item for item in resolved if item["label"] == "previous"), None)
        effective.update(compare_start=_iso(prior["start"]) if prior else None,
                         compare_end=_iso(prior["end"]) if prior else None)
        saved_request = {"_input": request_value, "_effective": effective}
        safe_question = body.question if preserve else redact_text(body.question)
        with store.connection(write=True) as connection:
            # Recheck after optional inference; a concurrent identical request wins once.
            exact = connection.execute("SELECT role,content,result,request,id,created_at,request_id FROM conversation_messages WHERE conversation_id=? AND request_id=?",
                                       (conversation_id_s, request_id)).fetchall()
            if exact:
                user = next((row for row in exact if row["role"] == "user"), None)
                existing = json.loads(user["request"]) if user and user["request"] else None
                existing_input = existing.get("_input", existing) if isinstance(existing, dict) else existing
                if existing_input != request_value:
                    raise HTTPException(409, "This request ID belongs to a different question.")
                assistant = next((row for row in exact if row["role"] == "assistant"), None)
                if assistant:
                    return {"user_message": _public_message(user), "assistant_message": _public_message(assistant),
                            "result": json.loads(assistant["result"])}
            now, user_id = utc_now(), str(uuid4())
            connection.execute("""INSERT INTO conversation_messages
                (id,project_id,conversation_id,request_id,role,content,request,result,created_at)
                VALUES(?,?,?,?, 'user', ?,?,NULL,?)""", (user_id, project, conversation_id_s, request_id,
                    safe_question, json.dumps(saved_request, sort_keys=True), now))
            assistant_id = str(uuid4())
            connection.execute("""INSERT INTO conversation_messages
                (id,project_id,conversation_id,request_id,role,content,request,result,created_at)
                VALUES(?,?,?,?, 'assistant', ?,NULL,?,?)""", (assistant_id, project, conversation_id_s, request_id,
                    result["answer"], json.dumps(result, sort_keys=True), now))
            from .context import save
            save(connection, conversation_id_s, safe_question, effective)
            connection.execute("UPDATE conversations SET updated_at=?,title=CASE WHEN title='New investigation' THEN ? ELSE title END WHERE id=?",
                               (now, safe_question[:200], conversation_id_s))
            user_message = {"id": user_id, "request_id": request_id, "role": "user", "content": safe_question, "result": None, "created_at": now}
            assistant_message = {"id": assistant_id, "request_id": request_id, "role": "assistant", "content": result["answer"], "result": result, "created_at": now}
            return {"user_message": user_message, "assistant_message": assistant_message, "result": result}

    app.post("/conversations/{conversation_id}/messages", dependencies=[Depends(control)])(conversation_turn)

    @app.post("/projects/{project_id}/conversations/{conversation_id}/messages", dependencies=[Depends(control)])
    async def project_turn(project_id: UUID, conversation_id: UUID, body: Turn):
        return await conversation_turn(conversation_id, body, project_id)

    @app.get("/projects/{project_id}/conversations/{conversation_id}/context", dependencies=[Depends(control)])
    def investigation_context(project_id: UUID, conversation_id: UUID, q: str = Query(default="", max_length=2000)):
        from .context import read
        with store.connection() as connection:
            row = connection.execute("SELECT 1 FROM conversations WHERE id=? AND project_id=?", (str(conversation_id), str(project_id))).fetchone()
            if not row:
                raise HTTPException(404, "Investigation not found.")
            return read(connection, str(conversation_id), q)

    @app.get("/settings/ui", dependencies=[Depends(control)])
    def ui_preferences(): return store.ui_preferences()

    @app.put("/settings/ui", dependencies=[Depends(control)])
    def update_ui(body: UIPreferences): return store.save_ui_preferences(body.environments_enabled)

    @app.get("/settings/capture", dependencies=[Depends(control)])
    def capture_settings():
        from .raw_capture import load_capture_policy, CaptureSpool
        try:
            return {**load_capture_policy(store.state_dir), "status":CaptureSpool(store.state_dir).status(),
                    "model_configured":(store.state_dir / "rag.json").exists()}
        except (ValueError,OSError):
            raise HTTPException(409,"Capture settings could not be read. Repair this instance's capture configuration before accepting more logs.") from None

    @app.put("/settings/capture", dependencies=[Depends(control)])
    async def update_capture(body: CapturePolicyInput):
        from .raw_capture import save_capture_policy
        try:
            save_capture_policy(store.state_dir, **body.model_dump())
        except ValueError:
            raise HTTPException(422,"Retention settings are incompatible or invalid. Use a compact shared model profile and limits of 1–1024 MiB and 1 minute–30 days.") from None
        if store.rag_runtime is None:
            from .rag_runtime import NativeRAGRuntime
            store.rag_runtime = NativeRAGRuntime(store)
            await store.rag_runtime.start()
        return capture_settings()

    @app.get("/settings/models/available", dependencies=[Depends(control)])
    async def available_models():
        from .shared_settings import list_models, SettingsError
        try:return await list_models(store)
        except SettingsError as error:raise HTTPException(error.status_code,str(error)) from None

    @app.put("/settings/shared-model", dependencies=[Depends(control)])
    async def shared_model(body: SharedModelInput):
        from .shared_settings import select_model, SettingsError
        previous=store.rag_runtime
        try:result=await select_model(store,endpoint=body.endpoint,model=body.model)
        except SettingsError as error:raise HTTPException(error.status_code,str(error)) from None
        if previous is None and store.rag_runtime is not None:await store.rag_runtime.start()
        return result

    @app.get("/settings/models", dependencies=[Depends(control)])
    def model_settings(): return store.settings()

    @app.put("/settings/models", dependencies=[Depends(control)])
    def update_models(body: ModelInput):
        if store.rag_runtime is not None:
            raise HTTPException(409,"Native models use one shared profile. Stop this instance and run logchat install to change it.")
        base_url = body.base_url
        if body.provider == "extractive":
            base_url = None
        elif body.provider == "ollama":
            base_url = base_url or "http://127.0.0.1:11434"
            LocalModels(base_url=base_url, chat_model=body.chat_model)
        else:
            if not base_url or not body.chat_model:
                raise HTTPException(422, "OpenAI-compatible models require a base URL and chat model.")
            try: validate_endpoint(base_url)
            except ValueError as error: raise HTTPException(422, str(error)) from None
            if body.api_key and base_url.startswith("http://"):
                raise HTTPException(422, "API keys require HTTPS.")
        return store.save_settings(body.provider, base_url, body.chat_model, body.embedding_model, body.api_key)

    @app.post("/settings/models/check", dependencies=[Depends(control)])
    async def check_models():
        settings = store.settings()
        if settings["provider"] == "unconfigured":
            return {"ok":False,"provider":"unconfigured","detail":"Configure the shared model with logchat install. Pending originals are not searchable yet."}
        if settings["provider"] == "extractive":
            return {"ok": True, "provider": "extractive", "detail": "Deterministic extractive answers are ready."}
        if settings["provider"] == "ollama":
            try:
                installed = await LocalModels(base_url=settings["base_url"] or "http://127.0.0.1:11434",
                                              chat_model=settings["chat_model"], embedding_model=settings["embedding_model"]).installed()
            except ModelUnavailable:
                return {"ok":False,"provider":"ollama","detail":"The configured local model endpoint is unavailable."}
            ready = bool(installed["chat"] and (installed["embedding"] or store.rag_runtime is None))
            return {"ok":ready,"provider":"ollama","models":installed}
        models = OpenAICompatibleModels(settings["base_url"], settings["chat_model"], store.model_api_key())
        try:
            result = await models.generate("Return a health check.", {"summaries": []}, {
                "type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False})
            return {"ok": bool(result["ok"]), "provider": "openai_compatible"}
        except Exception:
            return {"ok": False, "provider": "openai_compatible", "detail": "Configured model is unavailable."}

    @app.get("/guide", dependencies=[Depends(control)])
    def guide():
        from logchat.mcp import GUIDE
        detail = ("Native semantic mode uses one OS-user boundary, SQLite vectors and one shared local generation profile. Queries return cited context, not generated solutions."
                  if store.rag_runtime is not None else
                  "Legacy portable mode uses one OS-user boundary, SQLite aggregates and deterministic extractive context.")
        return {"guide": GUIDE + "\n\n" + detail}

    @app.get("/discovery", dependencies=[Depends(control)])
    def discovery():
        try:
            from .discovery import discover
            value = discover(exclude_port=port)
            return value if isinstance(value, dict) else {"projects": value, "notice": ""}
        except ImportError:
            return {"projects": [], "notice": "Portable project discovery is unavailable."}

    return app
