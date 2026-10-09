"""Version-one shared contracts; adapters supply LogEvent, never provider details.

Datetimes are aware UTC, windows are half open. Prepared batches declare their
representation: legacy templates/records or compact model prose and selected fields.
Storage scopes before ranking and publishes evidence/vectors/coverage atomically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol, Sequence
import math

from pipeline.types import LogEvent  # One adapter event representation.

CONTRACT_VERSION = "rag-v1"
MAX_EVENTS_PER_BATCH = 5_000
MAX_CHUNKS_PER_BATCH = 500
MAX_SUMMARY_CHARS = 16_000
MAX_MODEL_SUMMARY_CHARS = 1_600
MAX_COMPACT_EVIDENCE_CHARS = 16_000


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamps are required.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    owner_id: str
    project_id: str
    environment_id: str
    source_id: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v or len(v) > 200
               for v in (self.owner_id, self.project_id, self.environment_id, self.source_id)):
            raise ValueError("A bounded complete source identity is required.")


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self):
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        if self.start >= self.end:
            raise ValueError("A positive half-open time window is required.")


@dataclass(frozen=True, slots=True)
class ExactMetrics:
    event_count: int = 0
    duration_count: int = 0
    duration_sum_ms: float = 0.0
    duration_min_ms: float | None = None
    duration_max_ms: float | None = None
    status_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if (type(self.event_count) is not int or type(self.duration_count) is not int
                or not 0 <= self.duration_count <= self.event_count
                or not math.isfinite(self.duration_sum_ms) or self.duration_sum_ms < 0):
            raise ValueError("Invalid exact metrics.")
        if any(type(v) is not int or v < 0 for v in self.status_counts.values()):
            raise ValueError("Invalid status counts.")
        if sum(self.status_counts.values()) > self.event_count:
            raise ValueError("Status counts exceed observed events.")
        if any(not isinstance(key, str) or not key.isascii() or not key.isdigit()
               or not 100 <= int(key) <= 599 for key in self.status_counts):
            raise ValueError("Invalid HTTP status keys.")
        if self.duration_count == 0:
            if self.duration_sum_ms != 0 or self.duration_min_ms is not None or self.duration_max_ms is not None:
                raise ValueError("Duration metrics require measured events.")
        else:
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value < 0
                   for value in (self.duration_min_ms, self.duration_max_ms)):
                raise ValueError("Invalid measured duration bounds.")
            if self.duration_min_ms > self.duration_max_ms:
                raise ValueError("Duration bounds are reversed.")


@dataclass(frozen=True, slots=True)
class StageProvenance:
    stage: str
    status: Literal["ok", "empty", "deferred", "unavailable", "failed", "partial"]
    implementation: str
    model: str | None = None
    revision: str | None = None
    detail: str = ""  # Stable safe category; never raw provider exceptions.


@dataclass(frozen=True, slots=True)
class EmbeddingSpec:
    provider: str
    model: str
    revision: str
    dimensions: int
    distance: Literal["cosine"] = "cosine"

    def __post_init__(self):
        if (not self.provider or not self.model or not self.revision
                or type(self.dimensions) is not int or not 1 <= self.dimensions <= 4096
                or self.distance != "cosine"):
            raise ValueError("Invalid embedding specification.")


@dataclass(frozen=True, slots=True)
class SemanticChunk:
    chunk_id: str
    identity: SourceIdentity
    window: TimeWindow
    service: str
    level: str
    summary: str
    metrics: ExactMetrics
    pattern_id: str
    release: str | None = None
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    compression_version: str = "semantic-template-v1"
    loss_notes: tuple[str, ...] = ()
    supporting_records: str | None = None
    summary_model: str | None = None
    compact_evidence: str | None = None

    def __post_init__(self):
        if not self.chunk_id or not self.summary or len(self.summary) > MAX_SUMMARY_CHARS:
            raise ValueError("A bounded semantic summary and chunk identity are required.")
        if self.metrics.event_count < 1:
            raise ValueError("A semantic chunk must represent observed events.")
        if self.supporting_records is not None and (not isinstance(self.supporting_records, str)
                or not self.supporting_records or len(self.supporting_records) > MAX_SUMMARY_CHARS):
            raise ValueError("Supporting event records must be bounded and nonempty.")
        if self.summary_model is not None and (not isinstance(self.summary_model, str)
                or not self.summary_model or len(self.summary_model) > 160):
            raise ValueError("A bounded summary model identity is required.")
        if self.compact_evidence is not None and (not isinstance(self.compact_evidence, str)
                or not self.compact_evidence or len(self.compact_evidence) > MAX_COMPACT_EVIDENCE_CHARS):
            raise ValueError("Compact evidence must be bounded and nonempty.")
        for stamp in (self.first_event_at, self.last_event_at):
            if stamp is not None and not self.window.start <= utc(stamp) < self.window.end:
                raise ValueError("Observed timestamps must be inside the chunk window.")


@dataclass(frozen=True, slots=True)
class Coverage:
    identity: SourceIdentity
    window: TimeWindow
    status: Literal["complete", "empty", "gap", "failed"]
    metrics: ExactMetrics = field(default_factory=ExactMetrics)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PreparedBatch:
    batch_id: str
    identity: SourceIdentity
    window: TimeWindow
    chunks: tuple[SemanticChunk, ...]
    coverage: tuple[Coverage, ...]
    event_keys: tuple[str, ...] = ()  # Redacted event-ID hashes for replay detection.

    def __post_init__(self):
        if not self.batch_id or len(self.chunks) > MAX_CHUNKS_PER_BATCH or len(self.event_keys) > MAX_EVENTS_PER_BATCH:
            raise ValueError("Prepared batch exceeds bounded work limits.")
        if not self.coverage or any(x.identity != self.identity for x in (*self.chunks, *self.coverage)):
            raise ValueError("Prepared batch scope or coverage is invalid.")
        if any(x.window.start < self.window.start or x.window.end > self.window.end
               for x in (*self.chunks, *self.coverage)):
            raise ValueError("Prepared content must be contained by its batch window.")
        if sum(c.metrics.event_count for c in self.chunks) > MAX_EVENTS_PER_BATCH:
            raise ValueError("Prepared batch exceeds the event bound.")


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk: SemanticChunk
    vector: tuple[float, ...]
    spec: EmbeddingSpec

    def __post_init__(self):
        if (len(self.vector) != self.spec.dimensions
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                       for x in self.vector)
                or not any(x != 0 for x in self.vector)):
            raise ValueError("An embedding must have finite nonzero compatible dimensions.")


@dataclass(frozen=True, slots=True)
class BuildResult:
    batch_id: str
    chunks: tuple[EmbeddedChunk, ...]
    coverage: tuple[Coverage, ...]
    provenance: tuple[StageProvenance, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessingJob:
    job_id: str
    batch: PreparedBatch
    attempt: int
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RetrievalCell:
    cell_id: str
    owner_id: str
    project_id: str
    environment_id: str
    window: TimeWindow
    label: str = "current"
    source_ids: tuple[str, ...] = ()
    service: str | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: SemanticChunk
    score: float
    rank: int
    method: Literal["vector", "lexical"]


@dataclass(frozen=True, slots=True)
class Candidate:
    cell_id: str
    chunk: SemanticChunk
    score: float
    vector_score: float | None = None  # Cosine similarity, not distance.
    vector_rank: int | None = None
    lexical_rank: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    question: str
    cells: tuple[RetrievalCell, ...]
    candidates: tuple[Candidate, ...]
    coverage: tuple[Coverage, ...]
    embedding_spec: EmbeddingSpec
    provenance: tuple[StageProvenance, ...] = ()
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectedEvidence:
    candidate: Candidate
    reason: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class RemappedContext:
    retrieval: RetrievalResult
    selected: tuple[SelectedEvidence, ...]
    gaps: tuple[str, ...] = ()
    provenance: tuple[StageProvenance, ...] = ()


class EmbeddingProvider(Protocol):
    spec: EmbeddingSpec

    async def embed(self, texts: Sequence[str], *, purpose: Literal["document", "query"] = "document") -> Sequence[Sequence[float]]: ...


class StructuredModel(Protocol):
    chat_model: str

    async def generate(self, instruction: str, context: Any, schema: Mapping[str, Any]) -> Any: ...


class SearchBackend(Protocol):
    embedding_spec: EmbeddingSpec

    def vector_search(self, cell: RetrievalCell, vector: Sequence[float], *, limit: int) -> Sequence[SearchHit]: ...
    def lexical_search(self, cell: RetrievalCell, query: str, *, limit: int) -> Sequence[SearchHit]: ...
    def coverage_for(self, cell: RetrievalCell, *, limit: int = 1000) -> Sequence[Coverage]: ...
