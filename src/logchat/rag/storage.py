"""Transactional SQLite/FTS5/sqlite-vec backend; exact, scoped cosine KNN.

Only bounded result sets cross into Python. The vector extension performs an
exhaustive search over eligible vectors; this is not an approximate index.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import sqlite_vec

from logchat.rag.contracts import MAX_CHUNKS_PER_BATCH, Coverage, ExactMetrics, SearchHit, SemanticChunk, SourceIdentity, TimeWindow

MAX_SEARCH_LIMIT = 100
MAX_SOURCES = 64


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _key(*values):
    return hashlib.sha256(_json(values).encode()).hexdigest()


def _micros(stamp):
    # Avoid floating-point epoch rounding at half-open boundaries.
    from datetime import timezone
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400000000 + delta.seconds * 1000000 + delta.microseconds


def _encode_chunk(chunk):
    value = asdict(chunk)
    # Preserve historical immutable payload bytes for additive optional fields.
    for key in ("supporting_records", "summary_model", "compact_evidence"):
        if value.get(key) is None:
            value.pop(key, None)
    value["window"] = {"start": chunk.window.start.isoformat(), "end": chunk.window.end.isoformat()}
    value["first_event_at"] = chunk.first_event_at.isoformat() if chunk.first_event_at else None
    value["last_event_at"] = chunk.last_event_at.isoformat() if chunk.last_event_at else None
    return _json(value)


def _decode_chunk(payload):
    value = json.loads(payload)
    value["identity"] = SourceIdentity(**value["identity"])
    value["window"] = TimeWindow(**{key: datetime.fromisoformat(stamp) for key, stamp in value["window"].items()})
    value["metrics"] = ExactMetrics(**value["metrics"])
    value["loss_notes"] = tuple(value["loss_notes"])
    for key in ("first_event_at", "last_event_at"):
        value[key] = datetime.fromisoformat(value[key]) if value[key] else None
    return SemanticChunk(**value)


def _encode_coverage(coverage):
    value = asdict(coverage)
    value["window"] = {"start": coverage.window.start.isoformat(), "end": coverage.window.end.isoformat()}
    return _json(value)


def _decode_coverage(payload):
    value = json.loads(payload)
    value["identity"] = SourceIdentity(**value["identity"])
    value["window"] = TimeWindow(**{key: datetime.fromisoformat(stamp) for key, stamp in value["window"].items()})
    value["metrics"] = ExactMetrics(**value["metrics"])
    return Coverage(**value)


class SQLiteVectorStore:
    def __init__(self, db_path, embedding_spec):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.embedding_spec = embedding_spec
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("CREATE TABLE IF NOT EXISTS rag_index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                stored = connection.execute("SELECT value FROM rag_index_meta WHERE key='embedding_spec'").fetchone()
                expected = _json(asdict(embedding_spec))
                if stored and stored[0] != expected:
                    raise ValueError("embedding_index_model_mismatch")
                connection.execute("INSERT OR IGNORE INTO rag_index_meta VALUES('embedding_spec',?)", (expected,))
                connection.execute("""CREATE TABLE IF NOT EXISTS rag_chunks(
                    row_id INTEGER PRIMARY KEY, chunk_id TEXT NOT NULL UNIQUE,
                    batch_id TEXT NOT NULL, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    environment_id TEXT NOT NULL, source_id TEXT NOT NULL, service TEXT NOT NULL,
                    start_us INTEGER NOT NULL, end_us INTEGER NOT NULL, payload TEXT NOT NULL)""")
                connection.execute("CREATE INDEX IF NOT EXISTS rag_chunks_scope ON rag_chunks(owner_id,project_id,environment_id,source_id,start_us,end_us)")
                connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(summary,service,level)")
                connection.execute(f"""CREATE VIRTUAL TABLE IF NOT EXISTS rag_vectors USING vec0(
                    row_id INTEGER PRIMARY KEY, scope_key TEXT PARTITION KEY,
                    embedding FLOAT[{embedding_spec.dimensions}] distance_metric=cosine,
                    environment_id TEXT, source_id TEXT, service TEXT,
                    start_us INTEGER, end_us INTEGER)""")
                connection.execute("""CREATE TABLE IF NOT EXISTS rag_coverage(
                    coverage_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL, project_id TEXT NOT NULL, environment_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, start_us INTEGER NOT NULL, end_us INTEGER NOT NULL,
                    payload TEXT NOT NULL)""")
                connection.execute("CREATE INDEX IF NOT EXISTS rag_coverage_scope ON rag_coverage(owner_id,project_id,environment_id,source_id,start_us,end_us)")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("PRAGMA mmap_size=0")
            connection.execute("PRAGMA temp_store=FILE")
            self.db_path.chmod(0o600)
            for suffix in ("-wal", "-shm"):
                path = Path(str(self.db_path) + suffix)
                if path.exists():
                    path.chmod(0o600)
            return connection
        except Exception:
            connection.close()
            raise RuntimeError("sqlite_vector_backend_unavailable") from None

    def _blob(self, vector):
        if (len(vector) != self.embedding_spec.dimensions or
                any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector)):
            raise ValueError("invalid_query_or_stored_vector")
        magnitude = math.hypot(*vector)
        if not math.isfinite(magnitude) or magnitude == 0:
            raise ValueError("invalid_query_or_stored_vector")
        # Normalize before float32 conversion to avoid finite float64 overflow.
        return sqlite_vec.serialize_float32([x / magnitude for x in vector])

    def write_result(self, connection, result):
        if not connection.in_transaction:
            raise ValueError("active_publication_transaction_required")
        if len(result.chunks) > MAX_CHUNKS_PER_BATCH or not 1 <= len(result.coverage) <= MAX_CHUNKS_PER_BATCH:
            raise ValueError("publication_bound_exceeded")
        if len({item.chunk.chunk_id for item in result.chunks}) != len(result.chunks):
            raise ValueError("duplicate_result_chunk")
        from logchat.rag.scheduler import validate_durable_chunk
        for item in result.chunks:
            validate_durable_chunk(item.chunk)
        for item in result.chunks:
            if item.spec != self.embedding_spec:
                raise ValueError("embedding_index_model_mismatch")
            chunk, blob = item.chunk, self._blob(item.vector)
            payload = _encode_chunk(chunk)
            existing = connection.execute("SELECT row_id,batch_id,payload FROM rag_chunks WHERE chunk_id=?", (chunk.chunk_id,)).fetchone()
            if existing:
                if existing["batch_id"] != result.batch_id or existing["payload"] != payload:
                    raise ValueError("immutable_chunk_conflict")
                stored = connection.execute("SELECT embedding FROM rag_vectors WHERE row_id=?", (existing["row_id"],)).fetchone()
                if not stored or stored[0] != blob:
                    raise ValueError("immutable_vector_conflict")
                continue
            identity = chunk.identity
            cursor = connection.execute("""INSERT INTO rag_chunks(chunk_id,batch_id,owner_id,project_id,environment_id,source_id,service,start_us,end_us,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (chunk.chunk_id, result.batch_id, identity.owner_id, identity.project_id,
                identity.environment_id, identity.source_id, chunk.service, _micros(chunk.window.start), _micros(chunk.window.end), payload))
            row_id = cursor.lastrowid
            # Only compact evidence's approved fields are searchable alongside the
            # summary. Original supporting-record archives never enter new FTS rows.
            compact = getattr(chunk, "compact_evidence", None)
            connection.execute("INSERT INTO rag_chunks_fts(rowid,summary,service,level) VALUES(?,?,?,?)",
                (row_id, chunk.summary + ("\n" + compact if compact else ""), chunk.service, chunk.level))
            connection.execute("INSERT INTO rag_vectors(row_id,scope_key,embedding,environment_id,source_id,service,start_us,end_us) VALUES(?,?,?,?,?,?,?,?)",
                (row_id, _key(identity.owner_id, identity.project_id), blob, identity.environment_id, identity.source_id,
                 chunk.service, _micros(chunk.window.start), _micros(chunk.window.end)))
        for coverage in result.coverage:
            identity = coverage.identity
            payload = _encode_coverage(coverage)
            coverage_id = _key(result.batch_id, identity.owner_id, identity.project_id, identity.environment_id,
                               identity.source_id, coverage.window.start.isoformat(), coverage.window.end.isoformat())
            existing = connection.execute("SELECT payload FROM rag_coverage WHERE coverage_id=?", (coverage_id,)).fetchone()
            if existing and existing[0] != payload:
                raise ValueError("immutable_coverage_conflict")
            connection.execute("INSERT OR IGNORE INTO rag_coverage VALUES(?,?,?,?,?,?,?,?,?)", (coverage_id,
                result.batch_id, identity.owner_id, identity.project_id, identity.environment_id, identity.source_id,
                _micros(coverage.window.start), _micros(coverage.window.end), payload))

    @staticmethod
    def _limit(value, maximum=MAX_SEARCH_LIMIT):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("search_bound_exceeded")

    @staticmethod
    def _sources(cell):
        if len(cell.source_ids) > MAX_SOURCES:
            raise ValueError("source_filter_bound_exceeded")
        return tuple(sorted(set(cell.source_ids)))

    def vector_search(self, cell, vector, *, limit):
        self._limit(limit)
        blob = self._blob(vector)
        sources = self._sources(cell)
        results = {}
        with closing(self.connect()) as connection:
            # Per-source top k then global top k is exact for a union of sources.
            for source in sources or (None,):
                clauses = ["embedding MATCH ?", "k=?", "scope_key=?", "environment_id=?", "start_us<?", "end_us>?"]
                params = [blob, limit, _key(cell.owner_id, cell.project_id), cell.environment_id,
                          _micros(cell.window.end), _micros(cell.window.start)]
                if source is not None:
                    clauses.append("source_id=?"); params.append(source)
                if cell.service is not None:
                    clauses.append("service=?"); params.append(cell.service)
                rows = connection.execute("SELECT row_id,distance FROM rag_vectors WHERE " + " AND ".join(clauses), params).fetchall()
                for row in rows:
                    payload = connection.execute("SELECT payload FROM rag_chunks WHERE row_id=?", (row["row_id"],)).fetchone()
                    if payload is None:
                        raise RuntimeError("vector_chunk_integrity_failure")
                    chunk = _decode_chunk(payload[0])
                    results[chunk.chunk_id] = (chunk, max(-1.0, min(1.0, 1.0 - float(row["distance"]))))
        ordered = sorted(results.values(), key=lambda item: (-item[1], item[0].chunk_id))[:limit]
        return tuple(SearchHit(chunk, score, rank, "vector") for rank, (chunk, score) in enumerate(ordered, 1))

    def _filter(self, cell, *, alias=""):
        sources = self._sources(cell)
        clauses = [f"{alias}owner_id=?", f"{alias}project_id=?", f"{alias}environment_id=?", f"{alias}start_us<?", f"{alias}end_us>?"]
        params = [cell.owner_id, cell.project_id, cell.environment_id, _micros(cell.window.end), _micros(cell.window.start)]
        if sources:
            clauses.append(f"{alias}source_id IN ({','.join('?' for _ in sources)})")
            params.extend(sources)
        return clauses, params

    def lexical_search(self, cell, query, *, limit):
        self._limit(limit)
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("lexical_query_bound_exceeded")
        tokens = list(dict.fromkeys(re.findall(r"\w{2,80}", query, re.UNICODE)))[:64]
        if not tokens:
            return ()
        expression = " OR ".join('"' + token + '"' for token in tokens)
        clauses, params = self._filter(cell, alias="c.")
        if cell.service is not None:
            clauses.append("c.service=?"); params.append(cell.service)
        with closing(self.connect()) as connection:
            rows = connection.execute("""SELECT c.payload,bm25(rag_chunks_fts) AS score FROM rag_chunks_fts
                JOIN rag_chunks c ON c.row_id=rag_chunks_fts.rowid WHERE rag_chunks_fts MATCH ? AND """ +
                " AND ".join(clauses) + " ORDER BY score,c.chunk_id LIMIT ?", [expression, *params, limit]).fetchall()
        return tuple(SearchHit(_decode_chunk(row["payload"]), -row["score"], rank, "lexical") for rank, row in enumerate(rows, 1))

    def coverage_for(self, cell, *, limit=1000):
        self._limit(limit, maximum=10000)
        clauses, params = self._filter(cell)
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT payload FROM rag_coverage WHERE " + " AND ".join(clauses) +
                                      " ORDER BY start_us,end_us,coverage_id LIMIT ?", [*params, limit]).fetchall()
        # Returning exactly limit rows is ambiguous; callers must disclose the cap.
        return tuple(_decode_coverage(row[0]) for row in rows)

    def get_chunk(self, chunk_id, *, owner_id, project_id):
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT payload FROM rag_chunks WHERE chunk_id=? AND owner_id=? AND project_id=?",
                                     (chunk_id, owner_id, project_id)).fetchone()
        return _decode_chunk(row[0]) if row else None

    def list_chunks(self, *, owner_id, project_id, environment_id=None, limit=100):
        """Bounded, scoped memory browsing; this does not perform retrieval."""
        limit = min(MAX_SEARCH_LIMIT, max(1, limit))
        clauses = ["owner_id=?", "project_id=?"]
        params = [owner_id, project_id]
        if environment_id is not None:
            clauses.append("environment_id=?")
            params.append(environment_id)
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT payload FROM rag_chunks WHERE " + " AND ".join(clauses) +
                                      " ORDER BY end_us DESC,chunk_id LIMIT ?", [*params, limit]).fetchall()
        return tuple(_decode_chunk(row[0]) for row in rows)

    def statistics(self):
        with closing(self.connect()) as connection:
            result = {"chunks": connection.execute("SELECT count(*) FROM rag_chunks").fetchone()[0],
                      "vectors": connection.execute("SELECT count(*) FROM rag_vectors").fetchone()[0],
                      "coverage_rows": connection.execute("SELECT count(*) FROM rag_coverage").fetchone()[0],
                      "semantic_payload_bytes": connection.execute("SELECT coalesce(sum(length(cast(payload AS BLOB))),0) FROM rag_chunks").fetchone()[0],
                      "sqlite_vec_version": connection.execute("SELECT vec_version()").fetchone()[0]}
        result.update(database_bytes=self.db_path.stat().st_size, wal_bytes=Path(str(self.db_path) + "-wal").stat().st_size if Path(str(self.db_path) + "-wal").exists() else 0,
                      vector_payload_bytes=result["vectors"] * self.embedding_spec.dimensions * 4,
                      search="exact_cosine_knn", embedding_spec=asdict(self.embedding_spec))
        return result
