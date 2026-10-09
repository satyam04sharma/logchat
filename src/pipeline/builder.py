"""Dataset builder with fenced, atomic chunk replacement and cursor completion."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from cli.secrets import read_credential
from connectors.base import ConnectorError
from connectors.registry import make_connector
from pipeline.models import LocalModels
from pipeline.redaction import redact_event
from pipeline.store import PipelineStore
from pipeline.summary import SUMMARY_PROVENANCE, encode_summary


BUCKET_SECONDS = 900
MAX_EVENTS_PER_CHUNK = 200
MAX_EVENTS_PER_JOB = 5_000


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _bucket(ts: datetime) -> tuple[datetime, datetime]:
    start = datetime.fromtimestamp(int(ts.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS, tz=timezone.utc)
    return start, start + timedelta(seconds=BUCKET_SECONDS)


def _safe_summary(summary: str, exemplar: Any, credential: str | None) -> str:
    """Redact before storage without slicing deterministic structured facts."""
    try:
        payload = json.loads(summary)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("provenance") == SUMMARY_PROVENANCE:
        return encode_summary(payload, secrets=(credential,) if credential else ())
    return redact_event(replace(exemplar, message=summary), secrets=(credential,) if credential else ()).message


def _credential(source: dict[str, Any], job: dict[str, Any]) -> str | None:
    reference = source.get("credential_ref")
    if not reference:
        return None
    return read_credential(
        reference, owner_id=str(job["owner_id"]), project_id=str(job["project_id"]),
        environment_id=str(job["environment_id"]), source_id=str(job["source_id"]),
    )


async def _build_job(store: PipelineStore, models: LocalModels, job: dict[str, Any]) -> bool:
    source = await asyncio.to_thread(store.get_job_source, job["id"], job["lease_token"])
    if not source:
        return False
    try:
        credential = _credential(source, job)
        connector = make_connector(source["connector"], source["connector_config"], credential)
    except Exception:
        return await asyncio.to_thread(store.retry_job, job["id"], job["lease_token"], "source_configuration_unavailable", 30)
    groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    seen: set[str] = set()
    count = 0
    all_durations: list[float] = []
    all_statuses: dict[str, int] = defaultdict(int)
    retention_floor = datetime.now(timezone.utc) - timedelta(seconds=source["retention_seconds"])
    fetch_start = min(job["window_end"], max(job["window_start"], retention_floor))
    cursor_ts = job["window_start"]
    cursor_event_id: str | None = None
    try:
        async for event in connector.fetch(since=fetch_start, until=job["window_end"]):
            if event.ts < fetch_start or event.ts >= job["window_end"]:
                continue
            # Redaction hashes provider event identifiers before dedupe/cursor use, so
            # opaque provider values never cross the persistence boundary.
            event = redact_event(event, secrets=(credential,) if credential else ())
            identity = str(event.event_id)
            if identity in seen:
                continue
            seen.add(identity)
            start, end = _bucket(event.ts)
            key = (max(start, fetch_start), min(end, job["window_end"]), event.fingerprint, event.service, event.level, event.release or "")
            groups[key].append(event)
            count += 1
            if event.duration_ms is not None:
                all_durations.append(float(event.duration_ms))
            if event.request_status is not None:
                all_statuses[str(event.request_status)] += 1
            cursor_ts, cursor_event_id = event.ts, identity
            if count > MAX_EVENTS_PER_JOB:
                return await asyncio.to_thread(store.split_job, job["id"], job["lease_token"])
            if count % 500 == 0 and not await asyncio.to_thread(store.renew_job, job["id"], job["lease_token"]):
                return False
    except ConnectorError as exc:
        if exc.args and exc.args[0] in {"batch_limit_exceeded", "page_limit_exceeded"}:
            return await asyncio.to_thread(store.split_job, job["id"], job["lease_token"])
        return await asyncio.to_thread(store.retry_job, job["id"], job["lease_token"], "connector_fetch_failed", 30)
    except Exception:
        return await asyncio.to_thread(store.retry_job, job["id"], job["lease_token"], "connector_fetch_failed", 30)

    chunks: list[dict[str, Any]] = []
    for key, events in groups.items():
        for offset in range(0, len(events), MAX_EVENTS_PER_CHUNK):
            batch = events[offset:offset + MAX_EVENTS_PER_CHUNK]
            if not await asyncio.to_thread(store.renew_job, job["id"], job["lease_token"]):
                return False
            try:
                summary = _safe_summary(await models.summarize(batch), batch[0], credential)
                embedding = await models.embed(summary)
            except Exception:
                return await asyncio.to_thread(store.retry_job, job["id"], job["lease_token"], "model_processing_failed", 30)
            durations = [float(e.duration_ms) for e in batch if e.duration_ms is not None]
            statuses: dict[str, int] = defaultdict(int)
            for event in batch:
                if event.request_status is not None:
                    statuses[str(event.request_status)] += 1
            start, end, fingerprint, service, level, release = key
            chunks.append({
                "bucket_start": _iso(start), "bucket_end": _iso(end), "chunk_index": offset // MAX_EVENTS_PER_CHUNK,
                "fingerprint": fingerprint,
                "service": service, "level": level, "release": release, "event_count": len(batch),
                "duration_count": len(durations), "duration_sum_ms": sum(durations),
                "duration_min_ms": min(durations) if durations else None,
                "duration_max_ms": max(durations) if durations else None,
                "status_counts": dict(statuses), "summary": summary, "embedding": embedding,
            })

    effective_end = job["window_end"]
    coverage: list[dict[str, Any]] = []
    if job["requested_start"] < fetch_start:
        coverage.append({"window_start": _iso(job["requested_start"]), "window_end": _iso(fetch_start),
                         "status": "gap", "gap_reason": "cursor_expired", "detail": "source retention elapsed", "event_count": 0})
    if fetch_start < effective_end:
        coverage.append({"window_start": _iso(fetch_start), "window_end": _iso(effective_end),
                         "status": "complete" if count else "empty", "gap_reason": None, "detail": "", "event_count": count,
                         "duration_count": len(all_durations), "duration_sum_ms": sum(all_durations),
                         "duration_min_ms": min(all_durations) if all_durations else None,
                         "duration_max_ms": max(all_durations) if all_durations else None,
                         "status_counts": dict(all_statuses)})
    return await asyncio.to_thread(
        store.complete_job, job["id"], job["lease_token"], effective_end,
        cursor_event_id, chunks, coverage,
    )


async def build_job(store: PipelineStore, models: LocalModels, job: dict[str, Any]) -> bool:
    """Keep the fenced lease alive across connector and model network waits."""
    async def keep_lease() -> None:
        while True:
            await asyncio.sleep(60)
            if not await asyncio.to_thread(store.renew_job, job["id"], job["lease_token"]):
                return

    renewal = asyncio.create_task(keep_lease())
    try:
        return await _build_job(store, models, job)
    finally:
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal


async def build_once(store: PipelineStore, models: LocalModels, limit: int = 1) -> int:
    jobs = await asyncio.to_thread(store.claim_jobs, limit)
    completed = 0
    for job in jobs:
        completed += await build_job(store, models, job)
    return completed


async def run() -> None:
    store, models = PipelineStore(), LocalModels()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    poll_seconds = max(1, int(os.getenv("BUILDER_POLL_SECONDS", "5")))
    while not stop.is_set():
        await build_once(store, models)
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
