"""Independent adaptive summary scheduler (`python -m pipeline.scheduler`)."""
from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timedelta, timezone
from typing import Any

from connectors.registry import make_connector
from cli.secrets import read_credential
from pipeline.store import PipelineStore


TARGET_BATCH = 2_000
MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 21_600


def completed_volume(source: dict[str, Any], feedback: dict[str, Any] | None,
                     now: datetime) -> tuple[int | None, float, str]:
    """Validate scoped completed evidence; old windows never masquerade as probes."""
    if not feedback:
        return None, 0, "completed-window-missing"
    for key in ("source_id", "owner_id", "project_id", "environment_id"):
        if str(feedback.get(key)) != str(source[key]):
            return None, 0, "completed-window-scope-mismatch"
    end = feedback.get("window_end")
    duration = feedback.get("window_seconds")
    count = feedback.get("event_count")
    if (not isinstance(end, datetime) or end.tzinfo is None
            or not isinstance(duration, (int, float)) or not 0 < duration <= MAX_INTERVAL_SECONDS
            or type(count) is not int or not 0 <= count <= 5_000):
        return None, 0, "completed-window-invalid"
    max_age = min(MAX_INTERVAL_SECONDS, max(3_600, 2 * source["interval_seconds"]),
                  max(1, source["retention_seconds"] // 2))
    age = (now - end).total_seconds()
    if not 0 <= age <= max_age:
        return None, 0, "completed-window-stale"
    return count, duration, "completed-window"


def choose_interval(
    observed_events: int | None, window_seconds: float, retention_seconds: int,
    previous_seconds: int = 1_800,
) -> tuple[int, str]:
    """Return an adaptive interval while treating the retention cap as absolute."""
    retention_cap = max(1, retention_seconds // 2)
    floor = min(MIN_INTERVAL_SECONDS, retention_cap)
    ceiling = min(MAX_INTERVAL_SECONDS, retention_cap)
    if observed_events is None:
        ideal = previous_seconds
        signal_name = "volume-unknown"
    elif observed_events == 0:
        ideal = previous_seconds * 2
        signal_name = "quiet-backoff"
    else:
        rate_per_second = observed_events / max(1.0, window_seconds)
        ideal = TARGET_BATCH / rate_per_second
        signal_name = "target-batch"
    smoothed = round(0.35 * ideal + 0.65 * previous_seconds)
    chosen = max(floor, min(ceiling, smoothed))
    return chosen, f"{signal_name};retention-cap={retention_cap}s"


def _credential(source: dict[str, Any]) -> str | None:
    reference = source.get("credential_ref")
    if not reference:
        return None
    return read_credential(
        reference,
        owner_id=str(source["owner_id"]), project_id=str(source["project_id"]),
        environment_id=str(source["environment_id"]), source_id=str(source["source_id"]),
    )


async def schedule_once(store: PipelineStore, limit: int = 1) -> int:
    sources = await asyncio.to_thread(store.claim_due_sources, limit)
    now = datetime.now(timezone.utc)
    queued = 0
    for source in sources:
        since = source.get("cursor_ts") or now - timedelta(seconds=source["interval_seconds"])
        try:
            connector = make_connector(source["connector"], source["connector_config"], _credential(source))
            observed = await connector.probe(since=since, until=now)
        except Exception:
            # Probing is advisory. The builder still gets a chance to fetch; no provider
            # exception text crosses the persistence boundary.
            observed = None
        window_seconds = (now - since).total_seconds()
        provenance = "probe"
        if observed is None:
            try:
                feedback = await asyncio.to_thread(
                    store.completed_feedback, source["source_id"], source["lease_token"],
                )
                observed, window_seconds, provenance = completed_volume(source, feedback, now)
            except Exception:
                provenance = "completed-window-unavailable"
        interval, reason = choose_interval(
            observed, window_seconds, source["retention_seconds"], source["interval_seconds"],
        )
        reason = f"{provenance};{reason}"
        job_id = await asyncio.to_thread(
            store.enqueue_job, source["source_id"], source["lease_token"], now,
            observed, interval, reason,
        )
        queued += job_id is not None
    return queued


async def run() -> None:
    store = PipelineStore()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    poll_seconds = max(1, int(os.getenv("SCHEDULER_POLL_SECONDS", "15")))
    while not stop.is_set():
        try:
            await schedule_once(store)
        except Exception:
            # Database/provider outages are retried on the next poll. Exception
            # strings are deliberately neither logged nor persisted.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
