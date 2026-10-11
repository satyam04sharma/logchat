from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class NormalizationOrigin:
    """Internal pending-input provenance; never canonical replay evidence."""

    version: int
    prefix_chars: int
    binding: str


@dataclass(frozen=True, slots=True)
class LogEvent:
    event_id: str
    ts: datetime
    source: str
    service: str
    level: str
    message: str
    fingerprint: str
    release: str | None = None
    duration_ms: float | None = None
    request_status: int | None = None
    normalization_origin: NormalizationOrigin | None = field(default=None, compare=False, repr=False)
