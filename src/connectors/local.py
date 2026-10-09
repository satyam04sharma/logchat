"""Bounded adapters for logs produced by local processes and push clients.

This module is deliberately persistence-free.  It converts untrusted input into
the common ``LogEvent`` type; the local store owns redaction and aggregation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from pipeline.types import LogEvent

MAX_PUSH_EVENTS = 1000
MAX_MESSAGE_CHARS = 12000


def _timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                raise ValueError()
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        elif isinstance(value, str):
            text = value.strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
        else:
            raise ValueError()
    except (ValueError, OverflowError, OSError):
        raise ValueError("timestamp is invalid or outside the supported range") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 86_400_000:
        raise ValueError(f"{name} is outside the supported range")
    return number


def parse_event(value: dict[str, Any], *, source: str = "local",preserve_fields: bool=False) -> LogEvent:
    """Convert one structured push item to a bounded ``LogEvent``."""
    if not isinstance(value, dict):
        raise ValueError("event must be an object")
    message = value.get("message", value.get("msg", value.get("event")))
    if not isinstance(message, str) or not message.strip():
        raise ValueError("event message is required")
    if preserve_fields:
        known = {"message","msg","event","service","logger","component","level","severity","timestamp","ts","time","@timestamp",
                 "fingerprint","request_status","status_code","release","version","duration_ms","latency_ms","elapsed_ms"}
        extra = {key:item for key,item in value.items() if key not in known}
        if extra:
            message += "\nStructured fields: " + json.dumps(extra,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(message)>MAX_MESSAGE_CHARS:
            raise ValueError("Preserved event exceeds the input budget; split it explicitly.")
    else:
        message = message[:MAX_MESSAGE_CHARS]
    service = str(value.get("service") or value.get("logger") or value.get("component") or "application")[:200]
    level = str(value.get("level") or value.get("severity") or "info").lower()[:30]
    ts = _timestamp(value.get("timestamp", value.get("ts", value.get("time", value.get("@timestamp")))))
    event_id = str(value.get("event_id") or value.get("id") or uuid4())[:500]
    fingerprint = str(value.get("fingerprint") or hashlib.sha256(
        re.sub(r"\b\d+\b", "#", message.lower()).encode("utf-8", "replace")
    ).hexdigest())[:500]
    status = value.get("request_status", value.get("status_code"))
    if status is None and isinstance(value.get("status"), int) and not isinstance(value.get("status"), bool):
        status = value["status"] if not preserve_fields or 100 <= value["status"] <= 599 else None
    if status is not None:
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError("request_status must be an HTTP status")
    return LogEvent(
        event_id=event_id,
        ts=ts,
        source=str(source)[:200],
        service=service,
        level=level,
        message=message,
        fingerprint=fingerprint,
        release=str(value.get("release", value.get("version")))[:200]
                if value.get("release", value.get("version")) is not None else None,
        duration_ms=_number(value.get("duration_ms", value.get("latency_ms", value.get("elapsed_ms"))), "duration_ms"),
        request_status=status,
    )


def parse_stdout_line(line: str, *, source: str = "process",preserve_fields: bool=False) -> LogEvent:
    """Parse a JSON log line when possible, otherwise preserve it as text input."""
    if not isinstance(line, str) or not line.strip():
        raise ValueError("log line is empty")
    bounded = line.rstrip("\r\n")
    if preserve_fields and len(bounded)>MAX_MESSAGE_CHARS:
        raise ValueError("Preserved event exceeds the input budget; split it explicitly.")
    if not preserve_fields:
        bounded = bounded[:MAX_MESSAGE_CHARS]
    try:
        value = json.loads(bounded)
    except json.JSONDecodeError:
        value = {"message": bounded}
    if not isinstance(value, dict):
        value = {"message": bounded}
    # Common structured logger aliases.
    if "message" not in value:
        value["message"] = value.get("msg", bounded)
    return parse_event(value, source=source,preserve_fields=preserve_fields)


def parse_push_events(values: Iterable[dict[str, Any]], *, source: str = "push",preserve_fields: bool=False) -> list[LogEvent]:
    """Parse at most 1000 structured events without retaining their input container."""
    if not isinstance(values, list):
        values = list(values)
    if len(values) > MAX_PUSH_EVENTS:
        raise ValueError("a push may contain at most 1000 events")
    return [parse_event(value, source=source,preserve_fields=preserve_fields) for value in values]


# Backwards-friendly name for callers that think in terms of process output.
parse_line = parse_stdout_line
