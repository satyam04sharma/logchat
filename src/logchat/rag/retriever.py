"""Bounded scoped semantic/hybrid retrieval; scores are similarity, not truth.

Backends select within each authorized cell before top-k. This module never loads
the vector corpus, never collects source events, and never falls back to lexical
success when query embeddings fail. Authorization is the caller's responsibility;
backend results receive an additional fail-closed scope check here.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
import hashlib
from itertools import islice
import json
import math
import re
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from logchat.rag.contracts import (
    Candidate, Coverage, EmbeddingProvider, RetrievalCell,
    RetrievalResult, SearchBackend, SearchHit, SourceIdentity, StageProvenance,
    TimeWindow, utc,
)
from pipeline.redaction import redact_text

IMPLEMENTATION = "scoped-hybrid-rrf-v3"
_TOKEN = re.compile(r"[A-Za-z0-9][\w.-]{1,79}")


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    candidate_limit: int = 12  # Per cell; unrelated to final context budget.
    vector_limit: int = 48
    lexical_limit: int = 24
    coverage_limit: int = 1000
    max_cells: int = 16
    min_vector_similarity: float = 0.50
    rrf_constant: int = 60

    def __post_init__(self):
        for name, maximum in (("candidate_limit", 100), ("vector_limit", 100),
                              ("lexical_limit", 100), ("coverage_limit", 9999),
                              ("max_cells", 32), ("rrf_constant", 1000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("retrieval_configuration_bound_exceeded")
        if self.candidate_limit > self.vector_limit + self.lexical_limit:
            raise ValueError("candidate_limit_exceeds_ranked_pool")
        if (isinstance(self.min_vector_similarity, bool)
                or not math.isfinite(self.min_vector_similarity)
                or not -1 <= self.min_vector_similarity <= 1):
            raise ValueError("invalid_similarity_floor")


def previous_period(window: TimeWindow) -> TimeWindow:
    """Adjacent comparison with identical elapsed UTC duration."""
    return TimeWindow(window.start - (window.end - window.start), window.start)


def previous_month_window(window: TimeWindow, *, timezone_name: str = "UTC") -> TimeWindow:
    """Prior local-calendar month anchor, clipped at month end, same UTC duration.

    ZoneInfo's ordinary fold=0 policy is explicit for ambiguous calendar anchors.
    A nonexistent local time is normalized forward by its UTC round trip.
    """
    local = window.start.astimezone(ZoneInfo(timezone_name))
    year, month = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
    anchor = local.replace(year=year, month=month,
                           day=min(local.day, calendar.monthrange(year, month)[1]), fold=0)
    start = utc(anchor)
    return TimeWindow(start, start + (window.end - window.start))


def plan_cells(owner_id: str, project_id: str, environment_ids: Sequence[str],
               window: TimeWindow, *, source_ids: Sequence[str] | Mapping[str, Sequence[str]] = (),
               service: str | None = None, comparison_window: TimeWindow | None = None
               ) -> tuple[RetrievalCell, ...]:
    """Build the full environment × period product from caller-authorized scope.

    Pass the resulting cells unchanged for followups unless the user explicitly
    changes scope. User/assistant conversation text is never inserted as evidence.
    A source mapping can specify each environment's expected source inventory.
    """
    environments = tuple(environment_ids)
    if not environments or len(set(environments)) != len(environments) or len(environments) > 16:
        raise ValueError("invalid_environment_selection")
    windows = (("current", window),) + ((("previous", comparison_window),) if comparison_window else ())
    result = []
    for environment in environments:
        sources = tuple(source_ids.get(environment, ())) if isinstance(source_ids, Mapping) else tuple(source_ids)
        for label, period in windows:
            key = json.dumps([owner_id, project_id, environment, label, period.start.isoformat(),
                              period.end.isoformat(), sorted(sources), service], separators=(",", ":"))
            cell_id = label + "-" + hashlib.sha256(key.encode()).hexdigest()[:20]
            result.append(RetrievalCell(cell_id, owner_id, project_id, environment, period,
                                        label=label, source_ids=sources, service=service))
    _validate_cells(result, 32)
    return tuple(result)


def _validate_cells(cells, limit):
    if not cells or len(cells) > limit or len({cell.cell_id for cell in cells}) != len(cells):
        raise ValueError("invalid_retrieval_cells")
    # A query may compare environments, but must never merge security principals.
    if len({(cell.owner_id, cell.project_id) for cell in cells}) != 1:
        raise ValueError("mixed_owner_or_project_scope")
    for cell in cells:
        if (not isinstance(cell.cell_id, str) or not cell.cell_id or len(cell.cell_id) > 200
                or not isinstance(cell.label, str) or not cell.label or len(cell.label) > 200
                or len(cell.source_ids) > 64 or len(set(cell.source_ids)) != len(cell.source_ids)
                or (cell.service is not None and (not isinstance(cell.service, str)
                    or not cell.service or len(cell.service) > 200))):
            raise ValueError("invalid_retrieval_scope")
        for source in cell.source_ids or ("unspecified",):
            SourceIdentity(cell.owner_id, cell.project_id, cell.environment_id, source)
        if not isinstance(cell.window, TimeWindow):
            raise ValueError("invalid_retrieval_window")


def _in_scope(cell, item):
    identity = item.identity
    return (identity.owner_id == cell.owner_id and identity.project_id == cell.project_id
            and identity.environment_id == cell.environment_id
            and (not cell.source_ids or identity.source_id in cell.source_ids)
            and item.window.start < cell.window.end and item.window.end > cell.window.start)


def _bounded(values, limit):
    rows = tuple(islice(values, limit + 1))
    if len(rows) > limit:
        raise ValueError("backend_result_bound_exceeded")
    return rows


def _safe_chunk(chunk, *, preserve_content=False):
    from logchat.rag.sections import SECTION_VERSION, SUMMARY_VERSION, COMPACT_VERSION, records_for_chunk
    values = (chunk.summary, chunk.service, chunk.level, chunk.release or "")
    if not all(isinstance(value, str) for value in values):
        return False
    # A model-section label is a format check, never authorization. The caller
    # must independently opt in, and prepublication section formats stay out.
    if chunk.compression_version.startswith(("local-model-section", "local-model-summary", "local-model-compact")):
        if not preserve_content or chunk.compression_version not in {SECTION_VERSION, SUMMARY_VERSION, COMPACT_VERSION}:
            return False
        if chunk.compression_version in {SUMMARY_VERSION, COMPACT_VERSION}:
            try:
                records_for_chunk(chunk)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                return False
        return True
    return all(redact_text(value) == value for value in values)


def _scalars(value, path=(), depth=0):
    """Bounded string-field traversal without an entity-type vocabulary."""
    if depth > 16:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _scalars(item, (*path, str(key)), depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _scalars(item, (*path, "[]"), depth + 1)
    elif isinstance(value, str) and value:
        yield path, value
    # Numbers and booleans often describe counts/status rather than identity.
    # They remain evidence, without becoming inferred question constraints.


def _reject_nonstandard_json_constant(value):
    raise ValueError("nonstandard_json_constant")


def _record_values(record):
    values = {}
    # Canonical service/severity/time/measurements are context, not inferred
    # identity filters: error questions may need normal comparison baselines.
    for path, value in _scalars({key: record[key] for key in ("event_id", "fingerprint") if key in record}, ("record",)):
        values.setdefault(path, set()).add(value)
    # The adapter appends original extra fields as JSON. Also accept an original
    # JSON object message. Text merely resembling fields stays ordinary prose.
    if "important_fields" in record:
        for field in record["important_fields"]:
            for path, value in _scalars(field["value"], ("fields", field["key"])):
                values.setdefault(path, set()).add(value)
        return values
    message = record["message"]
    _, separator, suffix = message.rpartition("\nStructured fields: ")
    encoded = suffix if separator else message
    try:
        original = json.loads(encoded, parse_constant=_reject_nonstandard_json_constant)
    except (ValueError, TypeError, RecursionError):
        original = None
    if isinstance(original, dict):
        for path, value in _scalars(original, ("fields",)):
            values.setdefault(path, set()).add(value)
    return values


def _literal_in_question(value, question):
    # Word boundaries reject e.g. ID-42 inside ID-420; a continuation like .com
    # or /child also cannot turn a partial identifier into an exact request.
    return (len(value) <= len(question) and
            re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w|[.@:/+\-]\w)", question) is not None)


def _hits(cell, values, method, limit, *, preserve_content=False):
    rows = _bounded(values, limit)
    seen = set()
    for hit in rows:
        if (not isinstance(hit, SearchHit) or hit.method != method
                or type(hit.rank) is not int or not 1 <= hit.rank <= limit
                or hit.rank in seen or isinstance(hit.score, bool)
                or not math.isfinite(hit.score)):
            raise ValueError("invalid_backend_ranking")
        seen.add(hit.rank)
        if not _in_scope(cell, hit.chunk) or (cell.service is not None and hit.chunk.service != cell.service):
            raise ValueError("backend_scope_violation")
        if not _safe_chunk(hit.chunk, preserve_content=preserve_content):
            raise ValueError("unsafe_backend_content")
        if method == "vector" and not -1.000001 <= hit.score <= 1.000001:
            raise ValueError("invalid_cosine_similarity")
    if len({hit.chunk.chunk_id for hit in rows}) != len(rows):
        raise ValueError("duplicate_backend_chunk")
    return rows


def _identifiers(question):
    return {token.casefold() for token in _TOKEN.findall(question)
            if not token.startswith("REDACTED_") and ("_" in token
            or (any(c.isdigit() for c in token) and any(c.isalpha() for c in token))
            or (token.isupper() and len(token) >= 3) or re.search(r"[a-z][A-Z]", token))}


def _rank(cell, vector_hits, lexical_hits, question, config, *, preserve_content=False):
    from logchat.rag.sections import SUMMARY_VERSION, COMPACT_VERSION, records_for_chunk
    chunks, vectors, lexical = {}, {}, {}
    for hits, target in ((vector_hits, vectors), (lexical_hits, lexical)):
        for hit in hits:
            identifier = hit.chunk.chunk_id
            if identifier in chunks and chunks[identifier] != hit.chunk:
                raise ValueError("immutable_chunk_conflict")
            chunks[identifier] = hit.chunk
            target[identifier] = hit
    identifiers = _identifiers(question)
    candidates, rejected = [], 0
    for identifier, chunk in chunks.items():
        vector, lex = vectors.get(identifier), lexical.get(identifier)
        exact = bool(lex and identifiers.intersection(token.casefold() for token in
                     _TOKEN.findall(chunk.summary + " " + chunk.pattern_id)))
        if lex and preserve_content and chunk.compression_version in {SUMMARY_VERSION, COMPACT_VERSION} and not exact:
            # Summary embeddings can omit contacts and arbitrary literal values.
            # Require both an actual lexical hit and exact original-field match;
            # this admits candidates only, never bypassing model relevance.
            exact = any(_literal_in_question(value, question)
                        for record in records_for_chunk(chunk)
                        for values in _record_values(record).values() for value in values)
        if not exact and (vector is None or vector.score < config.min_vector_similarity):
            rejected += 1
            continue
        score = ((1 / (config.rrf_constant + vector.rank)) if vector else 0) + (
                 (1 / (config.rrf_constant + lex.rank)) if lex else 0)
        candidates.append(Candidate(cell.cell_id, chunk, score,
                                    vector_score=vector.score if vector else None,
                                    vector_rank=vector.rank if vector else None,
                                    lexical_rank=lex.rank if lex else None))
    candidates.sort(key=lambda c: (-c.score, -(c.vector_score if c.vector_score is not None else -1), c.chunk.chunk_id))
    return tuple(candidates[:config.candidate_limit]), rejected, len(candidates) > config.candidate_limit


def _complete(window, rows):
    cursor = window.start
    for item in sorted(rows, key=lambda row: (row.window.start, row.window.end)):
        if item.status not in {"complete", "empty"} or item.window.end <= cursor:
            continue
        if item.window.start > cursor:
            return False
        cursor = max(cursor, item.window.end)
        if cursor >= window.end:
            return True
    return False


def _coverage(cell, backend, limit):
    # One sentinel row proves exhaustion without loading the complete history.
    rows = _bounded(backend.coverage_for(cell, limit=limit + 1), limit + 1)
    capped = len(rows) > limit
    rows = rows[:limit]
    if any(not isinstance(row, Coverage) or not _in_scope(cell, row) for row in rows):
        raise ValueError("backend_coverage_scope_violation")
    gaps = []
    if capped:
        gaps.append("coverage_limit_reached_completeness_unknown")
    sources = cell.source_ids or tuple(sorted({row.identity.source_id for row in rows}))
    if not cell.source_ids:
        gaps.append("source_inventory_unknown")
    if not sources:
        gaps.append("coverage_missing")
    for source in sources:
        source_rows = tuple(row for row in rows if row.identity.source_id == source)
        # Gap names avoid interpolating provider descriptions, which are data.
        source_key = hashlib.sha256(source.encode()).hexdigest()[:12]
        if not source_rows:
            gaps.append(f"source_{source_key}_coverage_missing")
        elif not _complete(cell.window, source_rows):
            gaps.append(f"source_{source_key}_coverage_incomplete")
        if any(row.status in {"gap", "failed"} for row in source_rows):
            gaps.append(f"source_{source_key}_reported_gap_or_failure")
    if cell.service is not None:
        gaps.append("coverage_metrics_include_all_source_services")
    return rows, gaps, capped


async def retrieve(question: str, cells: Sequence[RetrievalCell], backend: SearchBackend,
                   provider: EmbeddingProvider, *, config: RetrievalConfig | None = None,
                   preserve_content: bool = False
                   ) -> RetrievalResult:
    """Retrieve immutable candidates and coverage with safe machine-readable status.

    Invalid caller scope/config raises ValueError before any model/backend call.
    Operational failures return explicit gaps and provenance, never raw errors.
    """
    if type(preserve_content) is not bool:
        raise ValueError("invalid_content_preservation_policy")
    config = config or RetrievalConfig()
    cells = tuple(cells)
    _validate_cells(cells, config.max_cells)
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("query_character_bound_exceeded")
    question = (question if preserve_content else redact_text(question)).strip()
    spec = backend.embedding_spec
    coverage, gaps, provenance = [], [], []
    coverage_counts = {}
    for cell in cells:
        try:
            rows, missing, capped = _coverage(cell, backend, config.coverage_limit)
            coverage.extend(rows)
            gaps.extend(f"{cell.cell_id}:{gap}" for gap in missing)
            coverage_counts[cell.cell_id] = (len(rows), capped)
        except Exception:
            gaps.append(f"{cell.cell_id}:coverage_unavailable_or_invalid")
            coverage_counts[cell.cell_id] = (0, False)

    def result(candidates=()):
        # The same immutable coverage row can overlap more than one cell.
        unique = {}
        for row in coverage:
            key = (row.identity, row.window, row.status, row.reason)
            if key not in unique:
                unique[key] = row
        return RetrievalResult(question, cells, tuple(candidates), tuple(unique.values()), spec,
                               tuple(provenance), tuple(dict.fromkeys(gaps)))

    if provider.spec != spec:
        gaps.append("embedding_model_mismatch")
        provenance.append(StageProvenance("retrieval", "unavailable", IMPLEMENTATION,
                                          model=provider.spec.model, revision=provider.spec.revision,
                                          detail="embedding_model_mismatch"))
        return result()
    try:
        vectors = await provider.embed((question,), purpose="query")
        if len(vectors) != 1:
            raise ValueError("query_embedding_count_mismatch")
        vector = tuple(vectors[0])
        if (len(vector) != spec.dimensions or any(isinstance(x, bool) or not isinstance(x, (int, float))
            or not math.isfinite(x) for x in vector) or not any(x != 0 for x in vector)):
            raise ValueError("invalid_query_embedding")
        if provider.spec != spec or backend.embedding_spec != spec:
            raise ValueError("embedding_model_changed_during_query")
    except Exception:
        gaps.append("query_embedding_unavailable_or_invalid")
        provenance.append(StageProvenance("retrieval", "unavailable", IMPLEMENTATION,
                                          model=spec.model, revision=spec.revision,
                                          detail="query_embedding_unavailable_or_invalid"))
        return result()
    provenance.append(StageProvenance("query_embedding", "ok", IMPLEMENTATION,
                                      model=spec.model, revision=spec.revision,
                                      detail="compatible_query_embedding"))
    candidates = []
    for cell in cells:
        try:
            vectors = _hits(cell, backend.vector_search(cell, vector, limit=config.vector_limit),
                            "vector", config.vector_limit, preserve_content=preserve_content)
            lexical = _hits(cell, backend.lexical_search(cell, question, limit=config.lexical_limit),
                            "lexical", config.lexical_limit, preserve_content=preserve_content)
            selected, rejected, capped = _rank(cell, vectors, lexical, question, config, preserve_content=preserve_content)
        except Exception:
            gaps.append(f"{cell.cell_id}:candidate_search_unavailable_or_invalid")
            provenance.append(StageProvenance("retrieval_cell", "failed", IMPLEMENTATION,
                                              detail=json.dumps({"cell_id": cell.cell_id,
                                                  "reason": "candidate_search_unavailable_or_invalid"})))
            continue
        candidates.extend(selected)
        if not selected:
            gaps.append(f"{cell.cell_id}:no_relevant_candidates")
        if any(c.chunk.window.start < cell.window.start or c.chunk.window.end > cell.window.end for c in selected):
            gaps.append(f"{cell.cell_id}:boundary_chunk_metrics_not_window_exact")
        detail = {"cell_id": cell.cell_id, "vector_returned": len(vectors),
                  "lexical_returned": len(lexical), "gate_rejected": rejected,
                  "candidates": len(selected), "candidate_cap_reached": capped,
                  "coverage_rows": coverage_counts[cell.cell_id][0],
                  "coverage_cap_reached": coverage_counts[cell.cell_id][1],
                  "score_meaning": "reciprocal_rank_fusion_not_confidence",
                  "similarity_floor": config.min_vector_similarity,
                  "content_policy": "preserved_local_sections" if preserve_content else "redacted_templates",
                  "revalidation_required": True}
        provenance.append(StageProvenance("retrieval_cell", "ok" if selected else "empty", IMPLEMENTATION,
                                          detail=json.dumps(detail, sort_keys=True, separators=(",", ":"))))
    provenance.append(StageProvenance("retrieval", "partial" if gaps else ("ok" if candidates else "empty"),
                                      IMPLEMENTATION, model=spec.model, revision=spec.revision,
                                      detail="bounded_scoped_hybrid_candidates_require_revalidation"))
    return result(candidates)
