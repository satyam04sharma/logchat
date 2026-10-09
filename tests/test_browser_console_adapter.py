import json
import pytest
from connectors.base import ConnectorError
from connectors.browser_console import BrowserConsoleAdapter


def test_plain_browser_console_keeps_source_independent_event_semantics():
    adapter = BrowserConsoleAdapter()
    row = {"timestamp": "2026-10-04T18:00:00Z", "level": "warn", "message": "Session credential lifetime elapsed"}
    event = adapter.normalize(row)
    assert event.message == row["message"] and event.level == "warning"
    assert event.service == "html-demo" and event == adapter.normalize(row)


def test_structured_console_metrics_and_service_binding():
    event = BrowserConsoleAdapter("selected-page").normalize({"timestamp": "2026-10-04T18:00:00Z", "level": "log", "message": json.dumps({"event_id": "one", "timestamp": "2026-10-04T18:00:00Z", "service": "untrusted-other-service", "message": "Welcome request completed", "duration_ms": 42, "request_status": 200})})
    assert event.event_id == "one" and event.service == "selected-page"
    assert event.duration_ms == 42 and event.request_status == 200


@pytest.mark.parametrize("record", [{"message": "event"}, {"timestamp": "bad", "message": "event"}, {"timestamp": "2026-10-04T18:00:00Z", "message": "event", "level": "invented"}])
def test_malformed_capture_is_safe_failure(record):
    with pytest.raises(ConnectorError, match="invalid_browser_console_record"):
        BrowserConsoleAdapter().normalize(record)


def test_batch_limit_does_not_silently_drop_records():
    row = {"timestamp": "2026-10-04T18:00:00Z", "message": "event"}
    with pytest.raises(ConnectorError, match="browser_console_batch_limit"):
        BrowserConsoleAdapter().normalize_batch([row, row], maximum=1)


def test_structured_console_preserves_selected_identifiers_outside_message():
    structured = {"message": "Account lookup failed", "email": "browser-user@example.test",
                  "phone": "+15550001111", "library_code": "GlyphIndexMismatch",
                  "details": {"request_id": "browser-42"}}
    event = BrowserConsoleAdapter().normalize({"timestamp": "2026-10-04T18:00:00Z",
                                              "message": json.dumps(structured)})
    for value in [structured["email"], structured["phone"], structured["library_code"], "browser-42"]:
        assert value in event.message
    assert event == BrowserConsoleAdapter().normalize({"timestamp": "2026-10-04T18:00:00Z",
                                                       "message": json.dumps(structured)})


def test_structured_console_budget_rejects_instead_of_truncating():
    with pytest.raises(ConnectorError, match="^invalid_browser_console_record$"):
        BrowserConsoleAdapter().normalize({"timestamp": "2026-10-04T18:00:00Z",
                                          "message": json.dumps({"message": "x" * 11950})})
