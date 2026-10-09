"""Explicit, private, bounded temporary originals; never a retrieval source.

The disk budget includes a conservative rollback-journal reserve, so usable
payload space is smaller than max_bytes. Deletion uses SQLite secure_delete;
this is not a forensic-erasure promise for filesystem snapshots or backups.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from uuid import uuid4

from logchat.rag.builder import event_key
from logchat.rag.contracts import SourceIdentity, utc
from pipeline.types import LogEvent

DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60
MAX_BATCH_EVENTS = 5000
MAX_BATCH_BYTES = 8 * 1024 * 1024
_CAPTURE_FILE = "capture.json"
SAFE_PROCESSING_FAILURES = frozenset({
    "compact_processing_unavailable", "capture_source_unavailable", "compact_model_profile_required_for_raw_capture",
    "section_timeout", "section_provider_unavailable", "section_invalid_partition", "section_invalid_model_output",
    "section_output_budget_exceeded", "section_model_input_budget_exceeded", "section_model_identity_required",
    "local_preserving_section_model_required", "compact_timeout", "compact_provider_unavailable",
    "compact_invalid_output", "compact_field_mismatch", "compact_output_budget_exceeded",
    "compact_input_budget_exceeded", "intake_backpressure_retry_required", "event_identity_content_conflict",
    "legacy_replay_digest_unavailable", "compact_intake_unavailable_retry_required",
    "intake_busy_retry_required", "intake_bridge_unavailable", "model_not_configured",
})


def processing_failure(error):
    """Content-free diagnostics for rejected intake, including pre-model contention."""
    category = getattr(error, "category", None) or str(error)
    if category not in SAFE_PROCESSING_FAILURES:
        category = "compact_processing_unavailable"
    phase = getattr(error, "phase", None)
    if phase not in {"partition", "review", "partition_repair", "review_repair", "review_assignment", "compact_summary", "summary"}:
        phase = "intake"
    return category, phase


def _validated(mode, max_bytes, retention_seconds):
    if mode not in {"summary_only", "retain_until_summarized"}:
        raise ValueError("invalid_capture_mode")
    if type(max_bytes) is not int or not 1024 * 1024 <= max_bytes <= 10 * 1024**3:
        raise ValueError("invalid_capture_byte_limit")
    if type(retention_seconds) is not int or not 60 <= retention_seconds <= 30 * 86400:
        raise ValueError("invalid_capture_retention")
    return {"version": 1, "mode": mode, "max_bytes": max_bytes, "retention_seconds": retention_seconds}


def load_capture_policy(state_dir: Path) -> dict:
    path = Path(state_dir) / _CAPTURE_FILE
    if not path.exists():
        return _validated("summary_only", DEFAULT_MAX_BYTES, DEFAULT_RETENTION_SECONDS)
    try:
        value = json.loads(path.read_text())
        return _validated(value["mode"], value["max_bytes"], value["retention_seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        # Corrupt settings never implicitly authorize persistence.
        raise ValueError("invalid_capture_configuration") from None


def save_capture_policy(state_dir: Path, *, mode="summary_only", max_bytes=DEFAULT_MAX_BYTES,
                        retention_seconds=DEFAULT_RETENTION_SECONDS) -> dict:
    value = _validated(mode, max_bytes, retention_seconds)
    directory = Path(state_dir).expanduser().resolve()
    spool_path = directory / "raw-capture.db"
    if spool_path.exists() and spool_path.stat().st_size > int(max_bytes * .45):
        raise ValueError("capture_limit_below_existing_allocation")
    profile = directory / "rag.json"
    if mode == "retain_until_summarized" and profile.exists():
        current = json.loads(profile.read_text())
        if current.get("content_policy") not in {"local_model_preserved", "local_model_compact"}:
            raise ValueError("compact_model_profile_required_for_raw_capture")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix=".capture-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, directory / _CAPTURE_FILE)
    finally:
        Path(name).unlink(missing_ok=True)
    return value


def content_digest(event: LogEvent) -> str:
    # File replay can assign a fresh observation time to the same stable line ID.
    value = {name: getattr(event, name) for name in
             ("source", "service", "level", "release", "fingerprint", "message", "duration_ms", "request_status")}
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _encoded_event(event):
    for name, limit in (("event_id", 1000), ("message", 12000), ("source", 200),
                        ("service", 200), ("level", 30), ("fingerprint", 1000)):
        value = getattr(event, name)
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ValueError("capture_invalid_event")
    if event.release is not None and (not isinstance(event.release, str) or len(event.release) > 200):
        raise ValueError("capture_invalid_event")
    if event.duration_ms is not None and (isinstance(event.duration_ms, bool)
            or not isinstance(event.duration_ms, (float, int))
            or not math.isfinite(event.duration_ms) or event.duration_ms < 0):
        raise ValueError("capture_invalid_event")
    if event.request_status is not None and (type(event.request_status) is not int or not 100 <= event.request_status <= 599):
        raise ValueError("capture_invalid_event")
    value = asdict(event)
    value["ts"] = utc(event.ts).isoformat()
    return _json(value)


@dataclass(frozen=True)
class CaptureClaim:
    token: str
    identity: SourceIdentity
    events: tuple[LogEvent, ...]
    event_keys: tuple[str, ...]


class CaptureSpool:
    """Adapters must authorize SourceIdentity before enqueue; scope binds every ID.

    Complete receipts contain only content digests and scope metadata until TTL.
    They provide replay protection after raw deletion; the semantic core's digest
    ledger remains authoritative after successful compact commits.
    """
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.path = self.state_dir / "raw-capture.db"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS raw_events(
                    event_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL, source_id TEXT NOT NULL, content_digest TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_bytes INTEGER NOT NULL, captured_at REAL NOT NULL,
                    expires_at REAL NOT NULL, available_at REAL NOT NULL, lease_token TEXT, lease_until REAL,
                    attempts INTEGER NOT NULL DEFAULT 0, last_failure TEXT, complete INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS raw_ready ON raw_events(complete,available_at,captured_at);
                CREATE INDEX IF NOT EXISTS raw_project ON raw_events(project_id);
                CREATE TABLE IF NOT EXISTS raw_counts(
                    project_id TEXT PRIMARY KEY, accepted_events INTEGER NOT NULL DEFAULT 0,
                    completed_events INTEGER NOT NULL DEFAULT 0, expired_events INTEGER NOT NULL DEFAULT 0,
                    expired_bytes INTEGER NOT NULL DEFAULT 0, rejected_events INTEGER NOT NULL DEFAULT 0,
                    failed_attempts INTEGER NOT NULL DEFAULT 0, last_failure TEXT);
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA secure_delete=ON")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            limit = load_capture_policy(self.state_dir)["max_bytes"]
            # Reserve > half the cap for journal pages, headers and metadata.
            connection.execute(f"PRAGMA max_page_count={max(16, int(limit * .45) // page_size)}")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _counts(connection, project_id):
        connection.execute("INSERT OR IGNORE INTO raw_counts(project_id) VALUES(?)", (project_id,))

    def _expire(self, connection, now, policy):
        # A shorter newly selected retention applies to existing rows as well.
        cutoff = now - policy["retention_seconds"]
        expired = connection.execute("""SELECT project_id,count(*) AS events,sum(payload_bytes) AS bytes
            FROM raw_events WHERE complete=0 AND (expires_at<=? OR captured_at<=?)
            GROUP BY project_id""", (now, cutoff)).fetchall()
        for row in expired:
            self._counts(connection, row["project_id"])
            connection.execute("""UPDATE raw_counts SET expired_events=expired_events+?,
                expired_bytes=expired_bytes+? WHERE project_id=?""", (row["events"], row["bytes"], row["project_id"]))
        connection.execute("DELETE FROM raw_events WHERE expires_at<=? OR captured_at<=?", (now, cutoff))
        return sum(row["events"] for row in expired)

    def expire(self, *, now=None):
        now = time.time() if now is None else now
        with self._connection() as connection:
            return self._expire(connection, now, load_capture_policy(self.state_dir))

    def _rejected(self, project_id, events, category):
        with self._connection() as connection:
            self._counts(connection, project_id)
            connection.execute("""UPDATE raw_counts SET rejected_events=rejected_events+?,last_failure=?
                WHERE project_id=?""", (events, category, project_id))

    def enqueue(self, identity: SourceIdentity, events, *, now=None) -> dict:
        policy = load_capture_policy(self.state_dir)
        if policy["mode"] != "retain_until_summarized":
            raise ValueError("raw_capture_not_enabled")
        now = time.time() if now is None else now
        if len(events) > MAX_BATCH_EVENTS:
            self._rejected(identity.project_id, len(events), "capture_batch_bound_exceeded")
            raise ValueError("capture_batch_bound_exceeded")
        unique, total_bytes = {}, 0
        try:
            for event in events:
                payload = _encoded_event(event)
                total_bytes += len(payload.encode("utf-8"))
                if total_bytes > MAX_BATCH_BYTES:
                    raise ValueError("capture_batch_bound_exceeded")
                key, digest = event_key(identity, event.event_id), content_digest(event)
                if key in unique and unique[key][0] != digest:
                    raise ValueError("event_identity_content_conflict")
                unique.setdefault(key, (digest, payload))
        except (ValueError, TypeError, AttributeError):
            self._rejected(identity.project_id, len(events), "capture_invalid_or_conflicting_batch")
            raise ValueError("capture_invalid_or_conflicting_batch") from None
        # The batch ID identifies content independently of incoming event order/time.
        batch_id = hashlib.sha256(_json(sorted((key, item[0]) for key, item in unique.items())).encode()).hexdigest()
        try:
            with self._connection() as connection:
                self._expire(connection, now, policy)
                self._counts(connection, identity.project_id)
                pending = []
                for key, (digest, payload) in unique.items():
                    old = connection.execute("SELECT content_digest FROM raw_events WHERE event_key=?", (key,)).fetchone()
                    if old is not None:
                        if old[0] != digest:
                            raise ValueError("event_identity_content_conflict")
                    else:
                        pending.append((key, digest, payload))
                # Reserve metadata space per event as well as the journal reserve.
                used = connection.execute("SELECT coalesce(sum(payload_bytes+1024),0) FROM raw_events").fetchone()[0]
                needed = sum(len(payload.encode("utf-8")) + 1024 for _, _, payload in pending)
                if pending and (used + needed > int(policy["max_bytes"] * .35)
                                or self.path.stat().st_size > int(policy["max_bytes"] * .45)):
                    raise ValueError("capture_capacity_exceeded")
                for key, digest, payload in pending:
                    connection.execute("""INSERT INTO raw_events(event_key,owner_id,project_id,environment_id,
                        source_id,content_digest,payload,payload_bytes,captured_at,expires_at,available_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (key, identity.owner_id, identity.project_id,
                        identity.environment_id, identity.source_id, digest, payload, len(payload.encode("utf-8")),
                        now, now + policy["retention_seconds"], now))
                connection.execute("UPDATE raw_counts SET accepted_events=accepted_events+? WHERE project_id=?",
                                   (len(pending), identity.project_id))
        except (ValueError, sqlite3.DatabaseError) as error:
            category = str(error) if str(error) in {"event_identity_content_conflict", "capture_capacity_exceeded"} else "capture_storage_unavailable"
            self._rejected(identity.project_id, len(events), category)
            raise ValueError(category) from None
        return {"accepted": len(events), "deduplicated": len(events) - len(pending), "batch_id": batch_id,
                "jobs": [], "state": "raw_pending", "indexed": False, "retrieval": "unavailable_until_summarized",
                "original_events_stored": True}

    def claim(self, *, now=None, limit=400, lease_seconds=600) -> CaptureClaim | None:
        if type(limit) is not int or not 1 <= limit <= 400 or not 301 <= lease_seconds <= 3600:
            raise ValueError("invalid_capture_claim_bound")
        now = time.time() if now is None else now
        with self._connection() as connection:
            self._expire(connection, now, load_capture_policy(self.state_dir))
            first = connection.execute("""SELECT * FROM raw_events WHERE complete=0 AND available_at<=?
                AND (lease_until IS NULL OR lease_until<=?) ORDER BY captured_at,event_key LIMIT 1""", (now, now)).fetchone()
            if first is None:
                return None
            scope = tuple(first[name] for name in ("owner_id", "project_id", "environment_id", "source_id"))
            rows = connection.execute("""SELECT * FROM raw_events WHERE complete=0 AND available_at<=?
                AND (lease_until IS NULL OR lease_until<=?) AND owner_id=? AND project_id=?
                AND environment_id=? AND source_id=? ORDER BY captured_at,event_key LIMIT ?""", (now, now, *scope, limit)).fetchall()
            token, keys, events, size = uuid4().hex, [], [], 0
            for row in rows:
                if size + row["payload_bytes"] > MAX_BATCH_BYTES:
                    break
                size += row["payload_bytes"]
                value = json.loads(row["payload"])
                value["ts"] = datetime.fromisoformat(value["ts"])
                events.append(LogEvent(**value))
                keys.append(row["event_key"])
                connection.execute("UPDATE raw_events SET lease_token=?,lease_until=? WHERE event_key=?",
                                   (token, now + lease_seconds, row["event_key"]))
            return CaptureClaim(token, SourceIdentity(*scope), tuple(events), tuple(keys))

    def complete(self, claim: CaptureClaim) -> int:
        """Call only after the corresponding compact enqueue has committed."""
        with self._connection() as connection:
            count = connection.execute("SELECT count(*) FROM raw_events WHERE lease_token=? AND complete=0", (claim.token,)).fetchone()[0]
            connection.execute("""UPDATE raw_events SET payload='',payload_bytes=0,complete=1,
                lease_token=NULL,lease_until=NULL,last_failure=NULL WHERE lease_token=? AND complete=0""", (claim.token,))
            connection.execute("UPDATE raw_counts SET completed_events=completed_events+? WHERE project_id=?", (count, claim.identity.project_id))
            return count

    def fail(self, claim: CaptureClaim, category="compact_processing_unavailable", *, now=None, retry_seconds=30):
        now = time.time() if now is None else now
        if category not in SAFE_PROCESSING_FAILURES:
            category = "compact_processing_unavailable"
        with self._connection() as connection:
            changed = connection.execute("""UPDATE raw_events SET lease_token=NULL,lease_until=NULL,
                available_at=?,attempts=attempts+1,last_failure=? WHERE lease_token=? AND complete=0""",
                (now + max(1, min(retry_seconds, 3600)), category, claim.token)).rowcount
            if changed:
                connection.execute("UPDATE raw_counts SET failed_attempts=failed_attempts+1,last_failure=? WHERE project_id=?",
                                   (category, claim.identity.project_id))
            return changed

    def status(self, project_id=None, *, now=None) -> dict:
        now = time.time() if now is None else now
        policy = load_capture_policy(self.state_dir)
        with self._connection() as connection:
            self._expire(connection, now, policy)
            where, args = (" WHERE project_id=?", (project_id,)) if project_id is not None else ("", ())
            row = connection.execute("""SELECT coalesce(sum(CASE WHEN complete=0 THEN 1 ELSE 0 END),0) AS pending_events,
                coalesce(sum(payload_bytes),0) AS pending_bytes,min(CASE WHEN complete=0 THEN captured_at END) AS oldest_captured_at,
                coalesce(sum(CASE WHEN complete=0 AND lease_token IS NOT NULL THEN 1 ELSE 0 END),0) AS leased_events,
                coalesce(sum(CASE WHEN complete=0 AND attempts>0 THEN 1 ELSE 0 END),0) AS pending_failed_events
                FROM raw_events""" + where, args).fetchone()
            counts = connection.execute("""SELECT coalesce(sum(accepted_events),0) AS accepted_events,
                coalesce(sum(completed_events),0) AS completed_events,coalesce(sum(expired_events),0) AS expired_events,
                coalesce(sum(expired_bytes),0) AS expired_bytes,coalesce(sum(rejected_events),0) AS rejected_events,
                coalesce(sum(failed_attempts),0) AS failed_attempts FROM raw_counts""" + where, args).fetchone()
            pending_failures = connection.execute("SELECT DISTINCT last_failure FROM raw_events" + where +
                (" AND" if where else " WHERE") + " complete=0 AND last_failure IS NOT NULL", args).fetchall()
            failures = connection.execute("SELECT DISTINCT last_failure FROM raw_counts" + where +
                (" AND" if where else " WHERE") + " last_failure IS NOT NULL", args).fetchall()
        return {**policy, **dict(row), **dict(counts), "dropped_events": counts["expired_events"],
                "failure_categories": sorted(item[0] for item in failures),
                "pending_failure_categories": sorted(item[0] for item in pending_failures),
                "disk_bytes": self.path.stat().st_size,
                "searchable": False, "representation": "temporary_original_events",
                "pending_on_disable": "retained_until_summarized_or_expired",
                "capacity_policy": "reject_new_batch_atomically", "disk_budget_includes_journal_reserve": True}
