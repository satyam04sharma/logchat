"""Durable bounded scheduling for prepared semantic work, independent of queries.

SQLite leases are fenced by a random token and expire strictly at the deadline.
The index writer participates in this store's transaction using the same database.
New writes accept legacy redacted templates or validated compact local summaries.
Historical original-record payloads remain readable; they are never silently migrated.
Provider/model exception bodies are never persisted as diagnostic categories.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Mapping
import uuid

from .contracts import (
    BuildResult, Coverage, ExactMetrics, PreparedBatch, ProcessingJob,
    SemanticChunk, SourceIdentity, TimeWindow, utc,
)


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    target_events: int = 2000
    min_interval_seconds: int = 5
    max_interval_seconds: int = 300
    max_attempts: int = 3
    retry_seconds: int = 5

    def __post_init__(self):
        if (not 1 <= self.target_events <= 5000
                or not 1 <= self.min_interval_seconds <= self.max_interval_seconds <= 21600
                or not 1 <= self.max_attempts <= 10 or not 1 <= self.retry_seconds <= 3600):
            raise ValueError("Invalid scheduler bounds.")


def choose_interval(observed_events: int | None, window_seconds: float,
                    retention_seconds: int, previous_seconds: int = 30,
                    config: SchedulerConfig = SchedulerConfig()) -> tuple[int, str]:
    """Measured rate drives cadence; unknown differs from successful empty input."""
    if retention_seconds < 1 or previous_seconds < 1:
        raise ValueError("Positive retention and cadence are required.")
    if observed_events is not None and (type(observed_events) is not int or observed_events < 0):
        raise ValueError("Volume must be a nonnegative integer or unknown.")
    cap = max(1, retention_seconds // 2)
    if observed_events is None:
        ideal, reason = previous_seconds, "volume_unknown"
    elif observed_events == 0:
        ideal, reason = previous_seconds * 2, "quiet_backoff"
    else:
        if window_seconds <= 0:
            raise ValueError("A measured volume requires a positive observation window.")
        ideal, reason = config.target_events * window_seconds / observed_events, "measured_volume"
    interval = round(.35 * ideal + .65 * previous_seconds)
    return max(min(config.min_interval_seconds, cap), min(config.max_interval_seconds, cap, interval)), reason


def _now(now: datetime | None) -> datetime:
    return utc(now or datetime.now(timezone.utc))


def _stamp(value: datetime) -> str:
    return utc(value).isoformat(timespec="microseconds")


def _identity_key(identity: SourceIdentity) -> str:
    return hashlib.sha256(json.dumps(asdict(identity), sort_keys=True).encode()).hexdigest()


def _encode(batch: PreparedBatch) -> str:
    value = asdict(batch)
    # Existing intake hashes must survive additive summary metadata.
    for chunk in value["chunks"]:
        for key in ("supporting_records", "summary_model", "compact_evidence"):
            if chunk.get(key) is None:
                chunk.pop(key, None)
    return json.dumps(value, default=lambda x: _stamp(x) if isinstance(x, datetime) else x,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode(payload: str) -> PreparedBatch:
    value = json.loads(payload)
    identity = SourceIdentity(**value["identity"])

    def window(item):
        return TimeWindow(datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))

    chunks = []
    for item in value["chunks"]:
        item["identity"] = SourceIdentity(**item["identity"])
        item["window"] = window(item["window"])
        item["metrics"] = ExactMetrics(**item["metrics"])
        item["loss_notes"] = tuple(item["loss_notes"])
        for key in ("first_event_at", "last_event_at"):
            item[key] = datetime.fromisoformat(item[key]) if item[key] else None
        chunks.append(SemanticChunk(**item))
    coverage = tuple(Coverage(SourceIdentity(**x["identity"]), window(x["window"]),
                              x["status"], ExactMetrics(**x["metrics"]), x["reason"])
                     for x in value["coverage"])
    return PreparedBatch(value["batch_id"], identity, window(value["window"]), tuple(chunks),
                         coverage, tuple(value["event_keys"]))


class OverlappingReplay(ValueError):
    """Re-prepare using only unseen event IDs; metrics cannot be partly deduplicated."""


def validate_durable_chunk(chunk: SemanticChunk) -> None:
    """One write policy for queues and index publication; reading old rows is separate."""
    from logchat.rag.sections import COMPACT_VERSION, RECORD_PREFIX, validate_compact_chunk
    if chunk.supporting_records is not None or RECORD_PREFIX in chunk.summary:
        raise ValueError("original_record_persistence_forbidden")
    if chunk.compression_version == COMPACT_VERSION:
        validate_compact_chunk(chunk)
    elif chunk.compression_version.startswith("local-") or getattr(chunk, "compact_evidence", None) is not None:
        raise ValueError("compact_local_summary_required_for_persistence")


def validate_durable_batch(batch: PreparedBatch) -> None:
    from logchat.rag.sections import COMPACT_VERSION, validate_compact_batch
    for chunk in batch.chunks:
        validate_durable_chunk(chunk)
    if any(chunk.compression_version == COMPACT_VERSION for chunk in batch.chunks):
        validate_compact_batch(batch)


class SQLiteSchedulerStore:
    def __init__(self, db_path: str | Path, *, connect: Callable[[], sqlite3.Connection] | None = None,
                 config: SchedulerConfig = SchedulerConfig()):
        self.db_path = Path(db_path)
        self.config = config
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._factory = connect
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS rag_schedule_sources(
                    scope TEXT PRIMARY KEY, identity TEXT NOT NULL,
                    retention_seconds INTEGER NOT NULL, interval_seconds INTEGER NOT NULL,
                    cursor TEXT, last_dispatched TEXT, last_observed_count INTEGER);
                CREATE TABLE IF NOT EXISTS rag_schedule_jobs(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL REFERENCES rag_schedule_sources(scope),
                    batch_id TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    event_count INTEGER NOT NULL, created_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('staged','ready','running','deferred','completed','failed')),
                    next_attempt TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT, lease_expires TEXT, error_category TEXT, completed_at TEXT,
                    UNIQUE(scope,batch_id));
                CREATE INDEX IF NOT EXISTS rag_schedule_due ON rag_schedule_jobs(state,next_attempt,sequence);
                CREATE INDEX IF NOT EXISTS rag_schedule_source ON rag_schedule_jobs(scope,sequence,state);
                CREATE TABLE IF NOT EXISTS rag_schedule_event_keys(
                    scope TEXT NOT NULL, event_key TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES rag_schedule_jobs(job_id),
                    PRIMARY KEY(scope,event_key));
                CREATE TABLE IF NOT EXISTS rag_schedule_event_digests(
                    scope TEXT NOT NULL, event_key TEXT NOT NULL, content_digest TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES rag_schedule_jobs(job_id),
                    PRIMARY KEY(scope,event_key));
                CREATE INDEX IF NOT EXISTS rag_schedule_digest_job ON rag_schedule_event_digests(job_id);
            """)
        if self.db_path.exists():
            self.db_path.chmod(0o600)

    @contextmanager
    def _connection(self, *, write: bool = False):
        connection = self._factory() if self._factory else sqlite3.connect(self.db_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("PRAGMA mmap_size=0")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def register_source(self, identity: SourceIdentity, *, retention_seconds: int = 86400,
                        interval_seconds: int = 30, now: datetime | None = None) -> None:
        _now(now)
        interval, _ = choose_interval(None, 0, retention_seconds, interval_seconds, self.config)
        with self._connection(write=True) as connection:
            connection.execute("""INSERT INTO rag_schedule_sources(scope,identity,retention_seconds,interval_seconds)
                VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                retention_seconds=excluded.retention_seconds""",
                (_identity_key(identity), json.dumps(asdict(identity), sort_keys=True), retention_seconds, interval))

    def enqueue(self, batch: PreparedBatch, *, now: datetime | None = None,
                event_digests: Mapping[str, str] | None = None) -> str:
        validate_durable_batch(batch)
        payload = _encode(batch)
        if len(payload.encode()) > 3_000_000:
            raise ValueError("Prepared payload exceeds the queue work budget.")
        if (len(set(batch.event_keys)) != len(batch.event_keys)
                or any(not re.fullmatch(r"[a-f0-9]{64}", key) for key in batch.event_keys)):
            raise ValueError("Event keys must be unique SHA-256 hashes.")
        if event_digests is not None and (set(event_digests) != set(batch.event_keys)
                or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                       for value in event_digests.values())):
            raise ValueError("Event digests must cover the exact batch keys.")
        scope, stamp = _identity_key(batch.identity), _stamp(_now(now))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        job_id = hashlib.sha256(f"{scope}:{batch.batch_id}".encode()).hexdigest()
        with self._connection(write=True) as connection:
            existing = connection.execute("SELECT job_id,payload_hash FROM rag_schedule_jobs WHERE scope=? AND batch_id=?",
                                          (scope, batch.batch_id)).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise ValueError("A batch identity cannot be reused for different semantic work.")
                if event_digests is not None:
                    stored = {row["event_key"]: row["content_digest"] for row in connection.execute(
                        "SELECT event_key,content_digest FROM rag_schedule_event_digests WHERE job_id=?", (existing["job_id"],))}
                    if not stored and event_digests:
                        raise ValueError("legacy_replay_digest_unavailable")
                    if stored != dict(event_digests):
                        raise ValueError("event_identity_content_conflict")
                return existing["job_id"]
            if not connection.execute("SELECT 1 FROM rag_schedule_sources WHERE scope=?", (scope,)).fetchone():
                raise ValueError("Register the authenticated source before enqueueing.")
            for offset in range(0, len(batch.event_keys), 500):
                keys = batch.event_keys[offset:offset + 500]
                placeholders = ",".join("?" for _ in keys)
                if connection.execute(f"SELECT 1 FROM rag_schedule_event_keys WHERE scope=? AND event_key IN ({placeholders}) LIMIT 1",
                                      (scope, *keys)).fetchone():
                    raise OverlappingReplay("Overlapping event IDs require re-preparation from unseen events.")
            count = sum(c.metrics.event_count for c in batch.chunks)
            connection.execute("""INSERT INTO rag_schedule_jobs(job_id,scope,batch_id,payload,payload_hash,
                event_count,created_at,state,next_attempt) VALUES(?,?,?,?,?,?,?,'staged',?)""",
                (job_id, scope, batch.batch_id, payload, digest, count, stamp, stamp))
            connection.executemany("INSERT INTO rag_schedule_event_keys VALUES(?,?,?)",
                                   ((scope, key, job_id) for key in batch.event_keys))
            if event_digests is not None:
                connection.executemany("INSERT INTO rag_schedule_event_digests VALUES(?,?,?,?)",
                    ((scope, key, event_digests[key], job_id) for key in batch.event_keys))
        return job_id

    def check_event_digests(self, identity: SourceIdentity, digests: Mapping[str, str]) -> tuple[str, ...]:
        """Verify replay content without retaining it; legacy missing digests fail explicitly."""
        if len(digests) > 5000 or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for pair in digests.items() for value in pair):
            raise ValueError("Invalid event-digest page.")
        keys, existing = tuple(digests), set()
        with self._connection() as connection:
            for offset in range(0, len(keys), 500):
                page = keys[offset:offset + 500]
                marks = ",".join("?" for _ in page)
                rows = connection.execute(f"""SELECT k.event_key,d.content_digest
                    FROM rag_schedule_event_keys k LEFT JOIN rag_schedule_event_digests d
                    ON d.scope=k.scope AND d.event_key=k.event_key
                    WHERE k.scope=? AND k.event_key IN ({marks})""", (_identity_key(identity), *page))
                for row in rows:
                    if row["content_digest"] is None:
                        raise ValueError("legacy_replay_digest_unavailable")
                    if row["content_digest"] != digests[row["event_key"]]:
                        raise ValueError("event_identity_content_conflict")
                    existing.add(row["event_key"])
        return tuple(key for key in keys if key not in existing)

    def unseen_event_keys(self, identity: SourceIdentity, keys: tuple[str, ...]) -> tuple[str, ...]:
        """Bounded source-scoped replay filter for adapters with overlapping polls."""
        if len(keys) > 5000 or any(not re.fullmatch(r"[a-f0-9]{64}", key) for key in keys):
            raise ValueError("Invalid event-key page.")
        existing = set()
        with self._connection() as connection:
            for offset in range(0, len(keys), 500):
                page = keys[offset:offset+500]
                marks = ",".join("?" for _ in page)
                existing.update(row[0] for row in connection.execute(
                    f"SELECT event_key FROM rag_schedule_event_keys WHERE scope=? AND event_key IN ({marks})",
                    (_identity_key(identity), *page)))
        return tuple(key for key in keys if key not in existing)

    def dispatch(self, *, now: datetime | None = None, limit: int = 32) -> tuple[str, ...]:
        if not 1 <= limit <= 256:
            raise ValueError("Dispatch limit must be between 1 and 256.")
        instant = _now(now)
        selected = []
        with self._connection(write=True) as connection:
            # One oldest unfinished job per source, ordered by last service time.
            # All grouping and scanning stays in SQL; Python only sees this page.
            rows = connection.execute("""SELECT j.job_id,j.scope,j.created_at,s.retention_seconds,
                    s.interval_seconds,(SELECT sum(p.event_count) FROM rag_schedule_jobs p
                        WHERE p.scope=j.scope AND p.state='staged') AS buffered
                FROM rag_schedule_jobs j JOIN rag_schedule_sources s ON s.scope=j.scope
                WHERE j.state='staged' AND NOT EXISTS(SELECT 1 FROM rag_schedule_jobs p
                    WHERE p.scope=j.scope AND p.sequence<j.sequence AND p.state!='completed')
                AND ((julianday(?) - julianday(j.created_at))*86400 + 0.0001 >=
                    min(s.interval_seconds,max(1,s.retention_seconds/2),?)
                    OR (SELECT sum(p.event_count) FROM rag_schedule_jobs p
                        WHERE p.scope=j.scope AND p.state='staged')>=?)
                ORDER BY coalesce(s.last_dispatched,''),j.sequence LIMIT ?""",
                (_stamp(instant), self.config.max_interval_seconds, self.config.target_events, limit)).fetchall()
            for row in rows:
                age = (instant - datetime.fromisoformat(row["created_at"])).total_seconds()
                due = min(row["interval_seconds"], max(1, row["retention_seconds"] // 2), self.config.max_interval_seconds)
                if row["buffered"] < self.config.target_events and age < due:
                    continue
                connection.execute("UPDATE rag_schedule_jobs SET state='ready' WHERE job_id=?", (row["job_id"],))
                connection.execute("UPDATE rag_schedule_sources SET last_dispatched=? WHERE scope=?",
                                   (_stamp(instant), row["scope"]))
                selected.append(row["job_id"])
        return tuple(selected)

    def claim_jobs(self, *, now: datetime | None = None, limit: int = 1,
                   lease_seconds: int = 300) -> tuple[ProcessingJob, ...]:
        if not 1 <= limit <= 256 or not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid worker claim bounds.")
        instant = _now(now)
        stamp, expires = _stamp(instant), _stamp(instant + timedelta(seconds=lease_seconds))
        jobs = []
        with self._connection(write=True) as connection:
            connection.execute("""UPDATE rag_schedule_jobs SET state='failed',lease_token=NULL,
                lease_expires=NULL,error_category='lease_attempts_exhausted'
                WHERE state='running' AND lease_expires<=? AND attempt>=?""", (stamp, self.config.max_attempts))
            rows = connection.execute("""SELECT j.* FROM rag_schedule_jobs j
                WHERE ((j.state IN ('ready','deferred') AND j.next_attempt<=?)
                    OR (j.state='running' AND j.lease_expires<=?))
                    AND j.attempt<? AND NOT EXISTS(SELECT 1 FROM rag_schedule_jobs p
                        WHERE p.scope=j.scope AND p.sequence<j.sequence AND p.state!='completed')
                ORDER BY j.next_attempt,j.sequence LIMIT ?""", (stamp, stamp, self.config.max_attempts, limit)).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                connection.execute("""UPDATE rag_schedule_jobs SET state='running',attempt=attempt+1,
                    lease_token=?,lease_expires=? WHERE job_id=?""", (token, expires, row["job_id"]))
                jobs.append(ProcessingJob(row["job_id"], _decode(row["payload"]), row["attempt"] + 1,
                                          token, datetime.fromisoformat(expires)))
        return tuple(jobs)

    @staticmethod
    def _leased(connection, job: ProcessingJob, instant: datetime):
        return connection.execute("""SELECT * FROM rag_schedule_jobs
            WHERE job_id=? AND state='running' AND lease_token=? AND lease_expires>?""",
            (job.job_id, job.lease_token, _stamp(instant))).fetchone()

    def refine(self, job: ProcessingJob, new_batch: PreparedBatch, *,
               now: datetime | None = None) -> ProcessingJob | None:
        """Fenced lossless section publication, without advancing the success cursor.

        The initial payload_hash stays immutable for intake replay validation.
        A claimed retry receives the refined payload, so successful grouping is
        not repeated after an embedding failure or process restart.
        """
        from logchat.rag.sections import COMPACT_VERSION, validate_refinement, validate_compact_batch
        validate_durable_batch(new_batch)
        instant = _now(now)
        with self._connection(write=True) as connection:
            row = self._leased(connection, job, instant)
            if not row:
                return None
            original = _decode(row["payload"])
            if original == new_batch and all(chunk.compression_version == COMPACT_VERSION for chunk in original.chunks):
                validate_compact_batch(original)
            else:
                if job.batch != original:
                    raise ValueError("refinement_job_payload_changed")
                validate_refinement(original, new_batch)
                payload = _encode(new_batch)
                if len(payload.encode()) > 3_000_000:
                    raise ValueError("refinement_payload_budget_exceeded")
                connection.execute("UPDATE rag_schedule_jobs SET payload=? WHERE job_id=?", (payload, job.job_id))
            return ProcessingJob(job.job_id, new_batch, row["attempt"], job.lease_token,
                                 datetime.fromisoformat(row["lease_expires"]))

    def complete(self, job: ProcessingJob, result: BuildResult,
                 writer: Callable[[sqlite3.Connection, BuildResult], None], *, now: datetime | None = None) -> bool:
        instant = _now(now)
        with self._connection(write=True) as connection:
            row = self._leased(connection, job, instant)
            if not row:
                return False
            batch = _decode(row["payload"])
            validate_durable_batch(batch)
            # Re-read durable input; a forged caller job cannot widen scope or alter metrics.
            if (job.batch != batch or result.batch_id != batch.batch_id or result.coverage != batch.coverage
                    or tuple(c.chunk for c in result.chunks) != batch.chunks):
                raise ValueError("Completion must preserve the exact prepared evidence and coverage.")
            if len({c.spec for c in result.chunks}) > 1:
                raise ValueError("A batch cannot mix embedding revisions.")
            if any(p.status in {"failed", "unavailable", "deferred"} for p in result.provenance):
                raise ValueError("Unavailable processing cannot be completed.")
            successful_count = sum(c.metrics.event_count for c in batch.coverage if c.status in {"complete", "empty"})
            if successful_count != row["event_count"] or any(c.status == "failed" for c in batch.coverage):
                raise ValueError("Successful coverage must conserve the batch event count.")
            cursor = batch.window.start
            for coverage in sorted(batch.coverage, key=lambda x: x.window.start):
                if coverage.window.start != cursor:
                    raise ValueError("Coverage must partition the prepared window without gaps or overlap.")
                cursor = coverage.window.end
            if cursor != batch.window.end:
                raise ValueError("Coverage must reach the prepared window boundary.")
            writer(connection, result)
            if not connection.in_transaction:
                raise RuntimeError("The index writer must not commit the scheduler transaction.")
            source = connection.execute("SELECT * FROM rag_schedule_sources WHERE scope=?", (row["scope"],)).fetchone()
            interval, _ = choose_interval(successful_count, (batch.window.end - batch.window.start).total_seconds(),
                                          source["retention_seconds"], source["interval_seconds"], self.config)
            connection.execute("""UPDATE rag_schedule_sources SET cursor=CASE WHEN cursor IS NULL OR cursor<? THEN ? ELSE cursor END,
                interval_seconds=?,last_observed_count=? WHERE scope=?""",
                (_stamp(cursor), _stamp(cursor), interval, successful_count, row["scope"]))
            # Drop duplicate pending payload; the backend owns committed evidence.
            connection.execute("""UPDATE rag_schedule_jobs SET state='completed',completed_at=?,payload='',
                lease_token=NULL,lease_expires=NULL,error_category=NULL WHERE job_id=?""", (_stamp(instant), job.job_id))
        return True

    def fail(self, job: ProcessingJob, category: str, *, now: datetime | None = None, retryable: bool = True) -> bool:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", category):
            raise ValueError("Only stable safe failure categories may be persisted.")
        instant = _now(now)
        with self._connection(write=True) as connection:
            row = self._leased(connection, job, instant)
            if not row:
                return False
            state = "deferred" if retryable and row["attempt"] < self.config.max_attempts else "failed"
            delay = min(3600, self.config.retry_seconds * 2 ** (row["attempt"] - 1))
            connection.execute("""UPDATE rag_schedule_jobs SET state=?,next_attempt=?,error_category=?,
                lease_token=NULL,lease_expires=NULL WHERE job_id=?""",
                (state, _stamp(instant + timedelta(seconds=delay)), category, job.job_id))
        return True

    def renew(self, job: ProcessingJob, *, now: datetime | None = None, lease_seconds: int = 300) -> bool:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid lease duration.")
        instant = _now(now)
        with self._connection(write=True) as connection:
            if not self._leased(connection, job, instant):
                return False
            connection.execute("UPDATE rag_schedule_jobs SET lease_expires=? WHERE job_id=?",
                               (_stamp(instant + timedelta(seconds=lease_seconds)), job.job_id))
        return True

    def retry_failed(self, identity: SourceIdentity, *, now: datetime | None = None, limit: int = 32) -> int:
        """Explicitly recover bounded terminal work after fixing source/model setup."""
        if not 1 <= limit <= 256:
            raise ValueError("Retry limit must be between 1 and 256.")
        with self._connection(write=True) as connection:
            changed = connection.execute("""UPDATE rag_schedule_jobs SET state='ready',attempt=0,
                next_attempt=?,error_category=NULL,lease_token=NULL,lease_expires=NULL
                WHERE job_id IN(SELECT job_id FROM rag_schedule_jobs WHERE scope=? AND state='failed'
                    ORDER BY sequence LIMIT ?)""", (_stamp(_now(now)), _identity_key(identity), limit))
            return changed.rowcount

    def status(self, identity: SourceIdentity) -> dict:
        with self._connection() as connection:
            scope = _identity_key(identity)
            source = connection.execute("SELECT cursor,last_observed_count,interval_seconds FROM rag_schedule_sources WHERE scope=?",
                                        (scope,)).fetchone()
            if source is None:
                return {"registered": False, "jobs": {}}
            counts = {row["state"]: row["count"] for row in connection.execute(
                "SELECT state,count(*) AS count FROM rag_schedule_jobs WHERE scope=? GROUP BY state", (scope,))}
            return {"registered": True, **dict(source), "jobs": counts,
                    "implementation": "semantic_prepared_queue_v1"}
