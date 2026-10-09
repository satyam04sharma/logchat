"""SQLite persistence for the portable, single-OS-user runtime.

Only redacted aggregates cross this boundary.  Raw ``LogEvent.message`` values
and provider event identifiers are never written to SQLite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from collections import defaultdict
from dataclasses import asdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException

from pipeline.redaction import redact_event, redact_text, sanitize
from pipeline.types import LogEvent

BUCKET_SECONDS = 900


def collection_time(value: str) -> datetime:
    """Parse persisted collection instants without inferring a timezone."""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('invalid_collection_timestamp') from None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class LocalStore:
    """Small transactional store; each operation gets an isolated connection."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.db_path = self.state_dir / "local.db"
        self.control_token_path = self.state_dir / "control-token"
        self.credentials_dir = self.state_dir / "credentials"
        self.credentials_dir.mkdir(exist_ok=True, mode=0o700)
        self.credentials_dir.chmod(0o700)
        self._schema_lock = threading.Lock()
        self._initialize()
        self.control_token = self._load_or_create_token()
        self.rag_runtime = None
        if (self.state_dir / "rag.json").exists() or (self.state_dir / "capture.json").exists():
            from .rag_runtime import NativeRAGRuntime
            self.rag_runtime = NativeRAGRuntime(self)

    def _load_or_create_token(self) -> str:
        try:
            value = self.control_token_path.read_text(encoding="utf-8").strip()
            if len(value) < 32:
                raise ValueError()
        except (OSError, ValueError):
            value = secrets.token_urlsafe(36)
            temporary = self.control_token_path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(value + "\n")
                os.replace(temporary, self.control_token_path)
            finally:
                temporary.unlink(missing_ok=True)
        self.control_token_path.chmod(0o600)
        return value

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        self.db_path.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                path.chmod(0o600)
        return connection

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def extend_collection_backoff(self, scope: str, retry_at: str) -> str:
        """Atomically retain the later instant, preserving existing text on ties."""
        proposed = collection_time(retry_at)
        with self.connection(write=True) as connection:
            current = connection.execute(
                'SELECT retry_at FROM collection_backoff WHERE scope=?', (scope,)
            ).fetchone()
            if current and collection_time(current['retry_at']) >= proposed:
                return current['retry_at']
            connection.execute('''INSERT INTO collection_backoff VALUES(?,?)
                ON CONFLICT(scope) DO UPDATE SET retry_at=excluded.retry_at''',
                               (scope, retry_at))
            return retry_at

    def _initialize(self) -> None:
        with self._schema_lock:
            connection = self._connect()
            try:
                connection.executescript("""
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS environments(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(project_id,name)
                );
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, kind TEXT NOT NULL, port INTEGER, token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(project_id,environment_id,name)
                );
                CREATE TABLE IF NOT EXISTS summaries(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    bucket_start TEXT NOT NULL, bucket_end TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, service TEXT NOT NULL, level TEXT NOT NULL,
                    release TEXT, summary TEXT NOT NULL, event_count INTEGER NOT NULL,
                    duration_count INTEGER NOT NULL, duration_sum_ms REAL NOT NULL,
                    duration_min_ms REAL, duration_max_ms REAL, status_counts TEXT NOT NULL,
                    UNIQUE(project_id,environment_id,source_id,bucket_start,fingerprint,service,level,release)
                );
                CREATE INDEX IF NOT EXISTS summaries_scope ON summaries(project_id,environment_id,bucket_start,bucket_end);
                CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(id UNINDEXED, summary, service, level);
                CREATE TABLE IF NOT EXISTS coverage(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    window_start TEXT NOT NULL, window_end TEXT NOT NULL,
                    status TEXT NOT NULL, gap_reason TEXT, event_count INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS coverage_scope ON coverage(project_id,environment_id,window_start);
                CREATE TABLE IF NOT EXISTS collection_cursors(
                    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                    cursor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_backoff(
                    scope TEXT PRIMARY KEY, retry_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_bindings(
                    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                    identity_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_polling(
                    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                    next_poll_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_recovery(
                    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                    window_seconds INTEGER NOT NULL CHECK(window_seconds BETWEEN 1 AND 3600),
                    failures INTEGER NOT NULL CHECK(failures BETWEEN 0 AND 7),
                    retry_at TEXT, error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    request TEXT, result TEXT, created_at TEXT NOT NULL,
                    UNIQUE(conversation_id,request_id,role)
                );
                CREATE TABLE IF NOT EXISTS ui_preferences(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), environments_enabled INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO ui_preferences VALUES(1,0);
                CREATE TABLE IF NOT EXISTS conversation_context(
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                    checkpoint TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS conversation_questions USING fts5(conversation_id UNINDEXED, content);
                CREATE TRIGGER IF NOT EXISTS conversation_question_insert AFTER INSERT ON conversation_messages
                    WHEN new.role='user' BEGIN
                    INSERT INTO conversation_questions(rowid,conversation_id,content) VALUES(new.rowid,new.conversation_id,new.content);
                END;
                INSERT INTO conversation_questions(rowid,conversation_id,content)
                    SELECT rowid,conversation_id,content FROM conversation_messages WHERE role='user'
                    AND rowid NOT IN (SELECT rowid FROM conversation_questions);
                CREATE TABLE IF NOT EXISTS model_settings(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), provider TEXT NOT NULL,
                    base_url TEXT, chat_model TEXT, embedding_model TEXT, has_api_key INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO model_settings(singleton,provider,has_api_key,updated_at)
                    VALUES(1,'extractive',0,datetime('now'));
                """)
            finally:
                connection.close()
            self.db_path.chmod(0o600)

    def project(self, connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT id,name,path,created_at FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found.")
        return dict(row)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,name,path,created_at FROM projects ORDER BY created_at,name"
            ).fetchall()]

    def create_project(self, name: str, path: str | None = None) -> dict[str, Any]:
        project_id, environment_id, now = str(uuid4()), str(uuid4()), utc_now()
        with self.connection(write=True) as connection:
            connection.execute("INSERT INTO projects VALUES(?,?,?,?)", (project_id, redact_text(name)[:200], redact_text(path)[:1000] if path else None, now))
            connection.execute("INSERT INTO environments VALUES(?,?,?,?)", (environment_id, project_id, "dev", now))
        return {"id": project_id, "name": redact_text(name)[:200], "path": redact_text(path)[:1000] if path else None,
                "environments": [{"id": environment_id, "name": "dev"}]}

    def list_environments(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            self.project(connection, project_id)
            return [dict(row) for row in connection.execute(
                "SELECT id,name,created_at FROM environments WHERE project_id=? ORDER BY created_at,name", (project_id,)
            ).fetchall()]

    def create_environment(self, project_id: str, name: str) -> dict[str, Any]:
        identifier, now = str(uuid4()), utc_now()
        try:
            with self.connection(write=True) as connection:
                self.project(connection, project_id)
                connection.execute("INSERT INTO environments VALUES(?,?,?,?)", (identifier, project_id, redact_text(name)[:100], now))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Environment already exists.") from None
        return {"id": identifier, "name": redact_text(name)[:100], "created_at": now}

    def _environment(self, connection: sqlite3.Connection, project_id: str, value: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT id,name FROM environments WHERE project_id=? AND (id=? OR name=?)", (project_id, value, value)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Environment not found.")
        return row

    def create_source(self, project_id: str, name: str, environment: str, kind: str, port: int | None) -> dict[str, Any]:
        identifier, token, now = str(uuid4()), secrets.token_urlsafe(36), utc_now()
        try:
            with self.connection(write=True) as connection:
                self.project(connection, project_id)
                env = self._environment(connection, project_id, environment)
                connection.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)", (
                    identifier, project_id, env["id"], redact_text(name)[:200], kind, port, token_hash(token), now,
                ))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Source already exists.") from None
        return {"id": identifier, "name": redact_text(name)[:200], "environment": env["name"],
                "port": port, "kind": kind, "token": token}

    def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            self.project(connection, project_id)
            rows = connection.execute("""
                    SELECT s.id,s.name,e.name AS environment,s.port,s.kind,s.created_at,
                  (SELECT count(*) FROM summaries c WHERE c.source_id=s.id) AS chunk_count,
                  (SELECT cursor FROM collection_cursors c WHERE c.source_id=s.id) AS collection_cursor
                FROM sources s JOIN environments e ON e.id=s.environment_id
                WHERE s.project_id=? ORDER BY s.created_at,s.name
            """, (project_id,)).fetchall()
            result = [dict(row) for row in rows]
            try:
                report = json.loads((self.state_dir/'railway-status.json').read_text())
                statuses = {item['source_id']: item['status'] for item in report.get('sources', []) if 'source_id' in item}
                for row in result:
                    row['collection_status'] = statuses.get(row['id'])
            except (OSError,ValueError,KeyError,TypeError):
                pass
            return result

    def authenticate_source(self, project_id: str, source_id: str, token: str) -> bool:
        supplied = token_hash(token)
        with self.connection() as connection:
            row = connection.execute(
                "SELECT token_hash FROM sources WHERE id=? AND project_id=?", (source_id, project_id)
            ).fetchone()
            return bool(row and secrets.compare_digest(row["token_hash"], supplied))

    @staticmethod
    def _bucket(value: datetime) -> tuple[str, str]:
        start = datetime.fromtimestamp(int(value.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS, tz=timezone.utc)
        return start.isoformat(), (start + timedelta(seconds=BUCKET_SECONDS)).isoformat()

    @staticmethod
    def _summary(event: LogEvent, count: int) -> str:
        # Store a deliberately lossy pattern, never an event sample.  The vocabulary
        # retains useful operational search terms while arbitrary request/user text
        # and unique canaries cannot cross the persistence boundary.
        vocabulary = (
            "timeout timed out slow latency duration connection connect disconnected database db sql cache redis "
            "http request response route endpoint server client worker queue job retry failed failure error exception "
            "warning unavailable refused reset memory cpu disk network auth permission denied rate limit startup shutdown "
            "broker celery task scheduler certificate ssl tls heartbeat healthcheck authentication traceback import module"
        ).split()
        lowered = redact_text(event.message).lower()
        found = []
        for term in vocabulary:
            if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered) and term not in found:
                found.append(term)
        # The fixed vocabulary already bounds storage. A positional cutoff loses
        # late operational topics and merges otherwise distinct event patterns.
        pattern = ", ".join(found) or "uncategorized application event"
        return redact_text(f"Observed {count} {event.level} event(s) in {event.service}. Aggregate pattern: {pattern}.")[:2000]

    def ingest(self, project_id: str, source_id: str, events: list[LogEvent], *,
               checkpoint: tuple[str | None, str] | None = None,
               collection_window_seconds: int | None = None,
               collection_next_poll_at: str | None = None) -> dict[str, int]:
        if self.rag_runtime is not None:
            if checkpoint is not None:
                raise RuntimeError("Semantic provider checkpoint integration is pending; collection cursor was not advanced.")
            return self.rag_runtime.ingest(project_id, source_id, events)
        safe_events = [redact_event(event) for event in events]
        groups: dict[tuple[str, ...], list[LogEvent]] = defaultdict(list)
        for event in safe_events:
            start, end = self._bucket(event.ts)
            # Native memory deliberately forgets arbitrary payload details. Group by
            # that same retained pattern so changing request IDs/traceback values
            # cannot flood retrieval with thousands of identical summaries.
            pattern_fingerprint = hashlib.sha256(self._summary(event, 1).encode()).hexdigest()
            groups[(start, end, pattern_fingerprint, event.service, event.level, event.release or "")].append(event)
        with self.connection(write=True) as connection:
            source = connection.execute(
                "SELECT id,environment_id FROM sources WHERE id=? AND project_id=?", (source_id, project_id)
            ).fetchone()
            if not source:
                raise HTTPException(404, "Source not found.")
            if checkpoint is not None:
                expected, next_cursor = checkpoint
                current = connection.execute("SELECT cursor FROM collection_cursors WHERE source_id=?", (source_id,)).fetchone()
                if (current["cursor"] if current else None) != expected:
                    raise RuntimeError("Collection cursor changed; retry from the saved checkpoint.")
                next_time = collection_time(next_cursor)
                if expected is not None and next_time <= collection_time(expected):
                    raise ValueError('collection_cursor_not_increasing')
                connection.execute("INSERT INTO collection_cursors VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET cursor=excluded.cursor",
                                   (source_id, next_cursor))
                if collection_window_seconds is not None:
                    # Reset failure state atomically with aggregate/cursor commit.
                    connection.execute("""INSERT INTO collection_recovery VALUES(?,?,0,NULL,NULL)
                        ON CONFLICT(source_id) DO UPDATE SET window_seconds=excluded.window_seconds,
                        failures=0,retry_at=NULL,error_code=NULL""", (source_id, collection_window_seconds))
                    # Normal polling state follows the same atomic checkpoint as recovery.
                    if collection_next_poll_at is None:
                        connection.execute("DELETE FROM collection_polling WHERE source_id=?", (source_id,))
                    else:
                        connection.execute("""INSERT INTO collection_polling VALUES(?,?)
                            ON CONFLICT(source_id) DO UPDATE SET next_poll_at=excluded.next_poll_at""",
                            (source_id, collection_next_poll_at))
            for key, batch in groups.items():
                start, end, fingerprint, service, level, release = key
                # Portable push buckets are mutable.  Clip their visible end to
                # the latest observation so a just-ingested event is queryable
                # immediately, while retaining the floored start for honest
                # partial-boundary disclosure.
                observed_end = (max(event.ts for event in batch) + timedelta(microseconds=1)).isoformat()
                durations = [event.duration_ms for event in batch if event.duration_ms is not None]
                statuses: dict[str, int] = defaultdict(int)
                for event in batch:
                    if event.request_status is not None:
                        statuses[str(event.request_status)] += 1
                existing = connection.execute("""
                    SELECT * FROM summaries WHERE project_id=? AND environment_id=? AND source_id=?
                      AND bucket_start=? AND fingerprint=? AND service=? AND level=? AND release=?
                """, (project_id, source["environment_id"], source_id, start, fingerprint, service, level, release)).fetchone()
                if existing:
                    total = existing["event_count"] + len(batch)
                    old_status = json.loads(existing["status_counts"])
                    for code, count in statuses.items():
                        old_status[code] = old_status.get(code, 0) + count
                    mins = [v for v in (existing["duration_min_ms"], min(durations) if durations else None) if v is not None]
                    maxes = [v for v in (existing["duration_max_ms"], max(durations) if durations else None) if v is not None]
                    connection.execute("""
                        UPDATE summaries SET bucket_end=?,summary=?,event_count=?,duration_count=?,duration_sum_ms=?,
                          duration_min_ms=?,duration_max_ms=?,status_counts=? WHERE id=?
                    """, (max(existing["bucket_end"], observed_end), self._summary(batch[0], total), total, existing["duration_count"] + len(durations),
                          existing["duration_sum_ms"] + sum(durations), min(mins) if mins else None,
                          max(maxes) if maxes else None, json.dumps(old_status, sort_keys=True), existing["id"]))
                    summary_id = existing["id"]
                else:
                    summary_id = str(uuid4())
                    connection.execute("""
                        INSERT INTO summaries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (summary_id, project_id, source["environment_id"], source_id, start, observed_end, fingerprint,
                          service, level, release, self._summary(batch[0], len(batch)), len(batch), len(durations),
                          sum(durations), min(durations) if durations else None, max(durations) if durations else None,
                          json.dumps(statuses, sort_keys=True)))
                row = connection.execute("SELECT summary,service,level FROM summaries WHERE id=?", (summary_id,)).fetchone()
                connection.execute("DELETE FROM summaries_fts WHERE id=?", (summary_id,))
                connection.execute("INSERT INTO summaries_fts(id,summary,service,level) VALUES(?,?,?,?)",
                                   (summary_id, row["summary"], row["service"], row["level"]))
            if safe_events:
                start = min(event.ts for event in safe_events).isoformat()
                end = (max(event.ts for event in safe_events) + timedelta(microseconds=1)).isoformat()
                connection.execute("""
                    INSERT INTO coverage(project_id,environment_id,source_id,window_start,window_end,status,gap_reason,event_count)
                    VALUES(?,?,?,?,?,'observed','continuous_capture_not_known',?)
                """, (project_id, source["environment_id"], source_id, start, end, len(safe_events)))
        return {"accepted": len(safe_events), "summaries": len(groups)}

    @staticmethod
    def _fts_query(query: str) -> str:
        # Question scaffolding and the aggregate template's "Observed events"
        # must not match every chunk and drown out the operational topic.
        stop = set(('a an the what which who when where why how is are was were be been being '
                    'do does did have has had can could would should will may might of in on at to '
                    'from for with without and or it its they them those these this that their '
                    'show shows tell explain describe compare compared observed observation observations '
                    'event events evidence supporting cite cited scope project services repeated '
                    'today yesterday current previous last past hour hours day days week weeks month months '
                    'were available measured measurements happened changed around latest before earlier again '
                    'return back investigate problem please me us you your about logs log '
                    'support supports supported mean average measurement '
                    'january february march april may june july august '
                    'september october november december').split())
        aliases = {'timeouts': ('timeout',), 'failures': ('failure','failed','error'),
                   'errors': ('error',), 'connections': ('connection',), 'requests': ('request',),
                   'warnings': ('warning', 'warn'), 'warning': ('warning', 'warn'),
                   'warn': ('warning', 'warn'),
                   'retries': ('retry',), 'workers': ('worker',), 'durations': ('duration',),
                   'latencies': ('latency',)}
        groups = []
        for word in re.findall(r"[A-Za-z0-9]{2,}", redact_text(query).lower()):
            if word in stop or word.isdecimal():
                continue
            group = aliases.get(word, (word,))
            if group not in groups:
                groups.append(group)
        # Severity is an explicit constraint even when a topic is named.
        # Generic failures retain their existing logical alternatives.
        severity = {'failure', 'failed', 'error', 'warning', 'warnings', 'warn', 'exception', 'exceptions'}
        topics = [group for group in groups if not set(group) <= severity]
        severity_groups = [group for group in groups if set(group) <= severity]
        if any(set(group) & {'failure', 'failed', 'error'} for group in severity_groups):
            severity_groups = [('failure', 'failed', 'error')] + [
                group for group in severity_groups if not set(group) & {'failure', 'failed', 'error'}]
        # Duration/latency measurements describe the requested metric when
        # there is a concrete topic; standalone metric queries remain lexical.
        metric_terms = {'duration', 'latency'}
        measurement_question = re.search(r'\b(mean|average|measurements?|measured)\b', query, re.I)
        concrete = [group for group in topics
                    if not (measurement_question and set(group) <= metric_terms)]
        if concrete:
            groups = concrete + severity_groups
        elif topics:
            groups = topics + severity_groups
        else:
            groups = severity_groups
        # Each concrete topic must occur in the same indexed memory. OR is
        # confined to explicit aliases, never used across distinct topics.
        return " AND ".join(
            "(" + " OR ".join(f'"{word}"' for word in group) + ")"
            for group in groups
        )

    def evidence(self, project_id: str, query: str = "", *, environment_ids: list[str] | None = None,
                 start: str | None = None, end: str | None = None, service: str | None = None,
                 limit: int = 100, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        owned = connection is None
        connection = connection or self._connect()
        try:
            self.project(connection, project_id)
            clauses, values = ["s.project_id=?"], [project_id]
            if environment_ids:
                clauses.append("s.environment_id IN (" + ",".join("?" for _ in environment_ids) + ")")
                values.extend(environment_ids)
            if start:
                clauses.append("s.bucket_start>=?"); values.append(start)
            if end:
                clauses.append("s.bucket_end<=?"); values.append(end)
            if service:
                clauses.append("s.service=?"); values.append(redact_text(service)[:200])
            fts = self._fts_query(query)
            join = ""
            if fts:
                join = " JOIN summaries_fts f ON f.id=s.id"
                clauses.append("summaries_fts MATCH ?"); values.append(fts)
            sql = f"""SELECT s.id,s.project_id,s.fingerprint,src.kind AS source_kind,e.name AS environment,s.environment_id,s.source_id,src.name AS source,
                    s.service,s.level,s.release,s.summary,s.event_count,s.bucket_start,s.bucket_end,
                    s.duration_count,s.duration_sum_ms,s.duration_min_ms,s.duration_max_ms,s.status_counts
                FROM summaries s {join}
                JOIN environments e ON e.id=s.environment_id JOIN sources src ON src.id=s.source_id
                WHERE {' AND '.join(clauses)} ORDER BY s.bucket_start DESC LIMIT ?"""
            rows = connection.execute(sql, (*values, min(max(limit, 1), 200))).fetchall()
            result = []
            for item in rows:
                value = dict(item)
                value["status_counts"] = json.loads(value["status_counts"])
                result.append(value)
            return sanitize(result)
        finally:
            if owned:
                connection.close()

    def memory(self, project_id: str, memory_id: str) -> dict[str, Any]:
        """Inspect one current aggregate, with project binding applied in the lookup."""
        with self.connection() as connection:
            self.project(connection, project_id)
            row = connection.execute("""
                SELECT s.id,s.project_id,s.environment_id,e.name AS environment,
                    s.source_id,src.name AS source,src.kind AS source_kind,
                    s.service,s.level,s.release,s.fingerprint,s.summary,
                    s.bucket_start,s.bucket_end,s.event_count,s.duration_count,
                    s.duration_sum_ms,s.duration_min_ms,s.duration_max_ms,s.status_counts
                FROM summaries s
                JOIN environments e ON e.id=s.environment_id AND e.project_id=s.project_id
                JOIN sources src ON src.id=s.source_id AND src.project_id=s.project_id
                WHERE s.project_id=? AND s.id=?
            """, (project_id, memory_id)).fetchone()
            # Unknown and other-project IDs deliberately have the same result.
            if row is None:
                raise HTTPException(404, "Supporting memory not found in this project.")
            memory = dict(row)
            memory["status_counts"] = json.loads(memory["status_counts"])
            return sanitize({"memory": memory, "provenance": {
                "kind": "native_aggregate", "storage": "sqlite",
                "retrieval": "exact_project_bound_id", "raw_logs": False,
                "snapshot": False,
                "limits": ["Current aggregate may grow as events arrive in its bucket.",
                           "Stored summaries are fixed-vocabulary sketches, not raw logs.",
                           "Observed counts and durations do not establish complete traffic or causation."],
            }})

    def partial_bucket_count(self, project_id: str, environment_id: str, start: str, end: str,
                             *, service: str | None = None, connection: sqlite3.Connection | None = None) -> int:
        owned = connection is None
        connection = connection or self._connect()
        try:
            clause = "project_id=? AND environment_id=? AND bucket_end>? AND bucket_start<? AND NOT (bucket_start>=? AND bucket_end<=?)"
            values: list[Any] = [project_id, environment_id, start, end, start, end]
            if service:
                clause += " AND service=?"; values.append(redact_text(service)[:200])
            return int(connection.execute(f"SELECT count(*) AS n FROM summaries WHERE {clause}", values).fetchone()["n"])
        finally:
            if owned:
                connection.close()

    def coverage(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            self.project(connection, project_id)
            return [dict(row) for row in connection.execute("""
                SELECT c.source_id,e.name AS environment,c.window_start,c.window_end,c.status,c.gap_reason,c.event_count
                FROM coverage c JOIN environments e ON e.id=c.environment_id
                WHERE c.project_id=? ORDER BY c.window_start DESC LIMIT 200
            """, (project_id,)).fetchall()]

    def create_conversation(self, project_id: str, title: str) -> dict[str, Any]:
        identifier, now = str(uuid4()), utc_now()
        with self.connection(write=True) as connection:
            self.project(connection, project_id)
            connection.execute("INSERT INTO conversations VALUES(?,?,?,?,?)",
                               (identifier, project_id, redact_text(title)[:200], now, now))
        return {"id": identifier, "title": redact_text(title)[:200], "created_at": now, "updated_at": now}

    def list_conversations(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            self.project(connection, project_id)
            return [dict(row) for row in connection.execute(
                "SELECT id,title,created_at,updated_at FROM conversations WHERE project_id=? ORDER BY updated_at DESC LIMIT 100",
                (project_id,)).fetchall()]

    def conversation(self, conversation_id: str, project_id: str | None = None,
                     connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        owned = connection is None
        connection = connection or self._connect()
        try:
            clause, values = "id=?", [conversation_id]
            if project_id:
                clause += " AND project_id=?"; values.append(project_id)
            row = connection.execute(
                f"SELECT id,project_id,title,created_at,updated_at FROM conversations WHERE {clause}", values
            ).fetchone()
            if not row:
                raise HTTPException(404, "Investigation not found.")
            messages = connection.execute("""
                SELECT id,request_id,role,content,result,created_at FROM conversation_messages
                WHERE conversation_id=? ORDER BY rowid DESC LIMIT 500
            """, (conversation_id,)).fetchall()
            output = []
            for message in reversed(messages):
                value = dict(message)
                value["result"] = json.loads(value["result"]) if value["result"] else None
                output.append(value)
            from .context import read
            total = connection.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
            return {"conversation": dict(row), "messages": output,
                    "page": {"direction": "latest", "limit": 500, "total_messages": total, "has_older": total > 500},
                    "context": read(connection, conversation_id)}
        finally:
            if owned:
                connection.close()

    def ui_preferences(self):
        with self.connection() as connection:
            return {"environments_enabled": bool(connection.execute("SELECT environments_enabled FROM ui_preferences WHERE singleton=1").fetchone()[0])}

    def save_ui_preferences(self, enabled: bool):
        with self.connection(write=True) as connection:
            connection.execute("UPDATE ui_preferences SET environments_enabled=? WHERE singleton=1", (int(enabled),))
        return self.ui_preferences()

    def settings(self) -> dict[str, Any]:
        if self.rag_runtime is not None:
            profile = self.rag_runtime.model_profile
            if profile is None:
                return {"provider":"unconfigured", "base_url":None,"chat_model":None,"embedding_model":None,
                        "has_api_key":False,"configuration_source":"shared_native_profile","read_only":True}
            return {"provider":"ollama","base_url":profile.config["base_url"],
                    "chat_model":profile.generation_model,"embedding_model":profile.config["embedding"]["model"],
                    "has_api_key":False,"updated_at":None,"configuration_source":"shared_native_profile",
                    "read_only":True}
        with self.connection() as connection:
            value = dict(connection.execute("SELECT provider,base_url,chat_model,embedding_model,has_api_key,updated_at FROM model_settings WHERE singleton=1").fetchone())
            value["has_api_key"] = bool(value["has_api_key"])
            return value

    def save_settings(self, provider: str, base_url: str | None, chat_model: str | None,
                      embedding_model: str | None, api_key: str | None) -> dict[str, Any]:
        credential = self.credentials_dir / "model-api-key"
        scope = {"provider": provider, "base_url": base_url}
        if provider == "extractive":
            credential.unlink(missing_ok=True)
        elif api_key is not None:
            if api_key:
                fd = os.open(credential, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump({**scope, "key": api_key}, output)
                credential.chmod(0o600)
            else:
                credential.unlink(missing_ok=True)
        elif credential.exists():
            try:
                saved = json.loads(credential.read_text(encoding="utf-8"))
                if any(saved.get(key) != value for key, value in scope.items()):
                    credential.unlink()
            except (OSError, ValueError, AttributeError):
                credential.unlink(missing_ok=True)
        has_key = credential.exists()
        with self.connection(write=True) as connection:
            connection.execute("""UPDATE model_settings SET provider=?,base_url=?,chat_model=?,embedding_model=?,
                has_api_key=?,updated_at=? WHERE singleton=1""", (provider, base_url, chat_model,
                embedding_model, int(has_key), utc_now()))
        return self.settings()

    def model_api_key(self) -> str | None:
        try:
            saved = json.loads((self.credentials_dir / "model-api-key").read_text(encoding="utf-8"))
            settings = self.settings()
            if saved.get("provider") != settings["provider"] or saved.get("base_url") != settings["base_url"]:
                return None
            value = saved.get("key")
            return value if isinstance(value, str) and value else None
        except (OSError, ValueError, AttributeError):
            return None

    def status(self, project_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            project = self.project(connection, project_id)
            sources = self.list_sources(project_id)
            aggregates = connection.execute("""SELECT count(*) AS total_aggregates,
                coalesce(sum(event_count),0) AS total_events,
                min(bucket_start) AS oldest_bucket_start, max(bucket_start) AS latest_bucket_start
                FROM summaries WHERE project_id=?""", (project_id,)).fetchone()
            conversations = connection.execute("""SELECT count(*) AS saved_conversations,
                (SELECT count(*) FROM conversation_messages m JOIN conversations c ON c.id=m.conversation_id
                 WHERE c.project_id=? AND m.project_id=?) AS saved_messages,
                (SELECT count(*) FROM conversation_context x JOIN conversations c ON c.id=x.conversation_id
                 WHERE c.project_id=?) AS saved_checkpoints
                FROM conversations WHERE project_id=?""", (project_id,)*4).fetchone()
            cursors = connection.execute("""SELECT count(*) AS sources_with_cursor,
                min(strftime('%Y-%m-%dT%H:%M:%fZ',cursor)) AS oldest,
                max(strftime('%Y-%m-%dT%H:%M:%fZ',cursor)) AS latest
                FROM collection_cursors c JOIN sources s ON s.id=c.source_id
                WHERE s.project_id=?""", (project_id,)).fetchone()
            # Order instants rather than their offset-bearing text. These are
            # committed window boundaries, not provider poll or event timestamps.
            memory = {
                "application": {**dict(aggregates), "bucket_seconds": BUCKET_SECONDS,
                    "application_ttl_seconds": None, "raw_logs_stored": False,
                    "retrieval": "native_lexical_not_vector",
                    "query_source": "persisted_aggregates"},
                "conversation": {**dict(conversations), "log_evidence": False,
                    "purpose": "user_context_checkpoint"},
                "collection": {"sources_with_cursor": cursors['sources_with_cursor'],
                    "oldest_committed_window_end": cursors['oldest'],
                    "latest_committed_window_end": cursors['latest'],
                    "last_provider_poll_at": None,
                    "limitations": "Cursor is a committed request boundary, not last event seen or last provider poll. "
                        "Empty windows can advance it; it proves neither retention nor complete coverage."},
            }
        if self.rag_runtime is not None and self.rag_runtime.backend is not None:
            connection = self.rag_runtime.backend.connect()
            try:
                indexed = connection.execute("""SELECT count(*) AS total_aggregates,
                    coalesce(sum(json_extract(payload,'$.metrics.event_count')),0) AS total_events,
                    min(json_extract(payload,'$.window.start')) AS oldest_bucket_start,
                    max(json_extract(payload,'$.window.end')) AS latest_bucket_end
                    FROM rag_chunks WHERE owner_id=? AND project_id=?""",("local-os-user",project_id)).fetchone()
                retained = connection.execute("""SELECT count(*) FROM rag_chunks WHERE owner_id=? AND project_id=?
                    AND json_extract(payload,'$.compression_version') IN
                    ('local-preserved-intake-v1','local-model-section-v1','local-model-summary-v1')""",
                    ("local-os-user", project_id)).fetchone()[0]
                queued = connection.execute("""SELECT count(DISTINCT j.job_id) FROM rag_schedule_jobs j,
                    json_each(CASE WHEN json_valid(j.payload) THEN j.payload ELSE '{}' END,'$.chunks') c
                    WHERE json_extract(CASE WHEN json_valid(j.payload) THEN j.payload ELSE '{}' END,'$.identity.project_id')=?
                    AND json_extract(c.value,'$.compression_version') IN
                    ('local-preserved-intake-v1','local-model-section-v1','local-model-summary-v1')""", (project_id,)).fetchone()[0]
            finally:
                connection.close()
            memory["legacy_application"] = memory["application"]
            memory["application"] = {**dict(indexed), "raw_logs_stored":bool(retained or queued),
                "full_event_text_stored":bool(retained or queued),
                "historical_full_text_chunks":retained, "historical_full_text_jobs":queued,
                "content_policy":self.rag_runtime.config.get("content_policy","redacted_templates"),
                "application_ttl_seconds":None, "retrieval":"semantic_hybrid",
                "query_source":"persisted_semantic_chunks", "embedding":asdict(self.rag_runtime.spec)}
            aggregates = indexed
        if self.rag_runtime is not None:
            pending = self.rag_runtime.raw_capture.status(project_id)
            memory["application"]["pending_raw_capture"] = pending
            if pending.get("pending_events", 0):
                memory["application"]["raw_logs_stored"] = True
                memory["application"]["full_event_text_stored"] = True
            memory["application"].setdefault("full_event_text_stored",False)
            if self.rag_runtime.backend is None:
                memory["application"].update(retrieval="unavailable_model_not_configured", query_source="none",
                    total_aggregates=0,total_events=0)
                aggregates={"total_aggregates":0}
        return {"project": {"id": project["id"], "name": project["name"]}, "sources": sources,
                "chunks": aggregates['total_aggregates'], "recent_jobs": [], "models": self.settings(),
                "memory": memory}
