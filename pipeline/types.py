from dataclasses import dataclass
from datetime import datetime


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
