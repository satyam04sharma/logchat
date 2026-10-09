"""Small trusted PostgreSQL boundary shared by scheduler and dataset builder."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class PipelineStore:
    """Call the private queue functions as the restricted worker login."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ["WORKER_DATABASE_URL"]

    def _call(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            return list(connection.execute(query, params).fetchall())

    def claim_due_sources(self, limit: int = 1, lease_seconds: int = 60) -> list[dict[str, Any]]:
        return self._call("select * from public.claim_due_sources(%s,%s)", (limit, lease_seconds))

    def enqueue_job(
        self, source_id: UUID | str, check_token: UUID | str, observed_at: datetime,
        observed_events: int | None, interval_seconds: int, reason: str,
    ) -> UUID | None:
        rows = self._call(
            "select public.enqueue_pipeline_job(%s,%s,%s,%s,%s,%s) as id",
            (source_id, check_token, observed_at, observed_events, interval_seconds, reason),
        )
        return rows[0]["id"]

    def completed_feedback(self, source_id: UUID | str, check_token: UUID | str) -> dict[str, Any] | None:
        rows = self._call(
            "select * from public.pipeline_completed_feedback(%s,%s)", (source_id, check_token),
        )
        return rows[0] if rows else None

    def claim_jobs(self, limit: int = 1, lease_seconds: int = 300) -> list[dict[str, Any]]:
        return self._call("select * from public.claim_pipeline_jobs(%s,%s)", (limit, lease_seconds))

    def get_job_source(self, job_id: UUID | str, lease_token: UUID | str) -> dict[str, Any] | None:
        rows = self._call("select * from public.get_job_source(%s,%s)", (job_id, lease_token))
        return rows[0] if rows else None

    def renew_job(self, job_id: UUID | str, lease_token: UUID | str, lease_seconds: int = 300) -> bool:
        rows = self._call(
            "select public.renew_pipeline_job(%s,%s,%s) as accepted", (job_id, lease_token, lease_seconds),
        )
        return bool(rows[0]["accepted"])

    def split_job(self, job_id: UUID | str, lease_token: UUID | str) -> bool:
        rows = self._call("select public.split_pipeline_job(%s,%s) as accepted", (job_id, lease_token))
        return bool(rows[0]["accepted"])

    def complete_job(
        self, job_id: UUID | str, lease_token: UUID | str, cursor_ts: datetime,
        cursor_event_id: str | None, chunks: list[dict[str, Any]], coverage: list[dict[str, Any]],
    ) -> bool:
        rows = self._call(
            "select public.complete_pipeline_job(%s,%s,%s,%s,%s::jsonb,%s::jsonb) as accepted",
            (job_id, lease_token, cursor_ts, cursor_event_id, json.dumps(chunks), json.dumps(coverage)),
        )
        return bool(rows[0]["accepted"])

    def retry_job(
        self, job_id: UUID | str, lease_token: UUID | str, error_summary: str,
        delay_seconds: int = 30,
    ) -> bool:
        # Callers pass stable redacted categories, never exception strings.
        rows = self._call(
            "select public.retry_pipeline_job(%s,%s,%s,%s) as accepted",
            (job_id, lease_token, error_summary[:500], delay_seconds),
        )
        return bool(rows[0]["accepted"])
