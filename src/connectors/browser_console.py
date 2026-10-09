"""Normalize explicitly captured browser console records for the common core.

The capture transport supplies timestamp/level/message. This adapter neither
opens browsers nor treats an HTTP listener as access to its page's console.
It retains no records; privacy redaction and durable preparation belong to the
shared Dataset Builder. Credentials are never console record fields.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime

from connectors.base import ConnectorError
from connectors.local import parse_event
from pipeline.types import LogEvent


class BrowserConsoleAdapter:
    def __init__(self, service: str = "html-demo") -> None:
        if not service or len(service) > 200:
            raise ConnectorError("invalid_browser_console_service")
        self.service = service

    def normalize(self, record: Mapping[str, object]) -> LogEvent:
        message = record.get("message")
        timestamp = record.get("timestamp")
        if not isinstance(message, str) or not message.strip() or len(message) > 12000:
            raise ConnectorError("invalid_browser_console_record")
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        if not isinstance(timestamp, str):
            raise ConnectorError("invalid_browser_console_record")
        level = str(record.get("level", "info")).lower()
        level = {"log": "info", "warn": "warning"}.get(level, level)
        if level not in {"trace", "debug", "info", "warning", "error", "fatal"}:
            raise ConnectorError("invalid_browser_console_record")
        values: dict[str, object] = {
            "event_id": hashlib.sha256((timestamp + "\0" + level + "\0" + message).encode()).hexdigest(),
            "timestamp": timestamp, "service": self.service,
            "level": level, "message": message,
        }
        # Preserve structured log values for selection by the shared compact
        # core. They are data, never provider configuration. parse_event enforces
        # the total preserved-input budget rather than silently dropping fields.
        try:
            structured = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            structured = None
        if isinstance(structured, dict) and "message" in structured:
            values.update(structured)
            values["service"] = self.service
        try:
            return parse_event(values, preserve_fields=isinstance(structured, dict) and "message" in structured)
        except (TypeError, ValueError, OverflowError):
            raise ConnectorError("invalid_browser_console_record") from None

    def normalize_batch(self, records: Iterable[Mapping[str, object]], *, maximum: int = 200) -> list[LogEvent]:
        if type(maximum) is not int or not 1 <= maximum <= 1000:
            raise ConnectorError("invalid_browser_console_batch")
        events: list[LogEvent] = []
        for record in records:
            if len(events) >= maximum:
                raise ConnectorError("browser_console_batch_limit")
            events.append(self.normalize(record))
        return events
