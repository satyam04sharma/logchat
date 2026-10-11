"""Internal normalization origin never changes canonical replay evidence."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json

import pytest

from connectors.local import (event_wire_fields, normalized_model_message, parse_event,
                              parse_push_events, validated_normalization_origin)
from logchat.local.raw_capture import CaptureSpool, content_digest, save_capture_policy
from logchat.rag.contracts import SourceIdentity
from pipeline.redaction import redact_event
from pipeline.types import NormalizationOrigin


def parsed(**updates):
    value = dict(message="operation failed", event_id="event-a", timestamp="2026-10-10T00:00:00Z",
                 service="api", duration_ms=1.5, status_code=503)
    value.update(updates)
    return parse_event(value, source="push", preserve_fields=True)


def test_canonical_message_fingerprint_digest_and_equality_unchanged():
    event = parsed()
    assert event.message == 'operation failed\nStructured fields: {"event_id": "event-a"}'
    assert event.fingerprint == hashlib.sha256(event.message.lower().encode()).hexdigest()
    legacy = replace(event, normalization_origin=None)
    assert event == legacy and content_digest(event) == content_digest(legacy)
    assert normalized_model_message(event) == "operation failed"
    assert normalized_model_message(legacy) == legacy.message


@pytest.mark.parametrize("prefix", [
    'producer\nStructured fields: {"event_id": "event-a"}',
    'repeat\nStructured fields: {}\nStructured fields: {"id":"event-a"}',
    'unicode λ\nnewline "quoted" \\slash {"event_id":"event-a"}',
])
def test_entire_producer_prefix_is_preserved(prefix):
    event = parsed(message=prefix)
    assert normalized_model_message(event) == prefix
    assert normalized_model_message(replace(event, normalization_origin=None)) == event.message


@pytest.mark.parametrize("fields,remaining", [
    ({"id": "event-a"}, {}),
    ({"id": "other"}, {"id": "other"}),
    ({"id": 123}, {"id": 123}),
    ({"id": True}, {"id": True}),
    ({"request_id": "event-a", "nested": {"id": "event-a"}, "rare": 0.5},
     {"request_id": "event-a", "nested": {"id": "event-a"}, "rare": 0.5}),
])
def test_only_exact_top_level_string_aliases_are_elided(fields, remaining):
    event = parsed(**fields)
    projected = normalized_model_message(event)
    assert projected == "operation failed" + ("\nStructured fields: " + json.dumps(
        remaining, ensure_ascii=False, sort_keys=True) if remaining else "")
    assert all(json.loads(event.message.split("\nStructured fields: ")[1])[key] == value
               for key, value in fields.items())


@pytest.mark.parametrize("identifier", [123, True, "x" * 501])
def test_nonstring_and_truncated_attribution_is_preserved(identifier):
    event = parsed(event_id=identifier)
    assert normalized_model_message(event) == event.message


@pytest.mark.parametrize("updates", [
    {"event_id": "new-id"}, {"message": "changed"}, {"source": "different"},
    {"release": "new"}, {"duration_ms": 2.0}, {"request_status": 200},
    {"fingerprint": "new"}, {"service": "new"}, {"level": "warning"},
])
def test_changed_attribution_or_content_falls_back(updates):
    event = replace(parsed(), **updates)
    assert validated_normalization_origin(event) is None
    assert normalized_model_message(event) == event.message


def test_redaction_timestamp_and_malformed_origin_fall_back():
    event = parsed()
    candidates = [redact_event(event), replace(event, ts=event.ts + timedelta(seconds=1))]
    for origin in [None, {"version": 1}, NormalizationOrigin(True, 16, "fake"),
                   NormalizationOrigin(1, -1, "fake"), NormalizationOrigin(1, 16, "fake")]:
        candidates.append(replace(event, normalization_origin=origin))
    for candidate in candidates:
        assert normalized_model_message(candidate) == candidate.message


def test_producer_claim_is_ordinary_source_data_and_cli_wire_excludes_internal_origin():
    claim = {"version": 1, "prefix_chars": 1, "binding": "fake"}
    event = parsed(normalization_origin=claim)
    assert json.loads(normalized_model_message(event).split("\nStructured fields: ")[1]) == {
        "normalization_origin": claim}
    wire = event_wire_fields(event)
    assert "normalization_origin" not in wire
    wire["timestamp"] = wire.pop("ts").isoformat()
    wire.pop("source")
    reparsed = parse_event(wire, preserve_fields=True)
    # The old wire's complete message is producer text on the next HTTP boundary.
    assert normalized_model_message(reparsed).startswith(event.message)


def test_actual_parser_400_canonical_events_offer_six_transient_views():
    events = parse_push_events([dict(message=f"operation {i % 6} failed", event_id=f"event-{i}",
                                    timestamp="2026-10-10T00:00:00Z") for i in range(400)],
                               preserve_fields=True)
    assert len({event.message for event in events}) == 400
    assert len({content_digest(event) for event in events}) == 400
    assert len({normalized_model_message(event) for event in events}) == 6


@pytest.mark.parametrize("legacy", [False, True])
def test_raw_spool_restart_origin_round_trip_and_completion_cleanup(tmp_path, legacy):
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    spool = CaptureSpool(tmp_path)
    event = parsed()
    if legacy:
        event = replace(event, normalization_origin=None)
    identity = SourceIdentity("owner", "project", "env", "source")
    spool.enqueue(identity, [event], now=1000)
    restart = CaptureSpool(tmp_path)
    claim = restart.claim(now=1001)
    recovered = claim.events[0]
    assert recovered.normalization_origin == event.normalization_origin
    assert content_digest(recovered) == content_digest(event)
    assert normalized_model_message(recovered) == normalized_model_message(event)
    assert restart.enqueue(identity, [event], now=1002)["deduplicated"] == 1
    assert restart.complete(claim) == 1
    assert b"normalization_origin" not in spool.path.read_bytes()


def test_stale_origin_is_not_serialized_into_raw_spool(tmp_path):
    save_capture_policy(tmp_path, mode="retain_until_summarized")
    spool = CaptureSpool(tmp_path)
    event = replace(parsed(), event_id="changed-attribution")
    spool.enqueue(SourceIdentity("owner", "project", "env", "source"), [event], now=1000)
    recovered = spool.claim(now=1001).events[0]
    assert recovered.normalization_origin is None
    assert recovered.message == event.message
