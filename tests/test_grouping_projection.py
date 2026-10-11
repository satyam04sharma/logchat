"""Fixture-model proof of transient grouping; no real throughput claim."""
from dataclasses import replace
from datetime import timedelta

import pytest

from connectors.local import normalized_model_message, parse_push_events
from logchat.local.raw_capture import content_digest
from logchat.rag.builder import event_key, prepare_preserved_batch
from logchat.rag.contracts import TimeWindow
from logchat.rag.sections import decode_records, section_batch, validate_refinement
from tests.test_rag_sections import IDENTITY, LocalPartitionModel


def parsed(count=400):
    return parse_push_events([
        dict(message=f"operation {i % 6} failed", event_id=f"event-{i}",
             timestamp="2026-10-10T00:00:00Z", duration_ms=i + .5,
             request_status=503 if i == count - 1 else 200)
        for i in range(count)], preserve_fields=True)


def prepare(events):
    window = TimeWindow(events[0].ts, events[0].ts + timedelta(minutes=1))
    return prepare_preserved_batch(IDENTITY, events, window)


def sidecar(events):
    return {event_key(IDENTITY, event.event_id): event for event in events}


@pytest.mark.asyncio
async def test_actual_parser_400_six_grouping_items_expand_all_canonical_records():
    events = parsed()
    original = prepare(events)
    digests = [content_digest(event) for event in events]
    model = LocalPartitionModel()
    refined = await section_batch(original, model, model_events=sidecar(events))
    assert model.calls == 2  # partition and coherence review, six representatives
    assert all(len(payload["items"]) == 6 for payload in model.payloads)
    assert {item["messages"][0] for item in model.payloads[0]["items"]} == {
        normalized_model_message(event) for event in events}
    validate_refinement(original, refined)
    assert len(refined.event_keys) == 400
    assert refined.coverage == original.coverage
    records = [row for chunk in refined.chunks for row in decode_records(chunk.summary)]
    assert len(records) == 400 and len({row["message"] for row in records}) == 400
    assert sum(row["duration_ms"] for row in records) == sum(event.duration_ms for event in events)
    assert sum(row["request_status"] == 503 for row in records) == 1
    assert [content_digest(event) for event in events] == digests
    again = await section_batch(prepare(list(reversed(events)) + [events[0]]),
                                LocalPartitionModel(), model_events=sidecar(events))
    assert again == refined


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"event_id": "other"}, {"message": "changed"}, {"source": "other"},
    {"service": "other"}, {"level": "error"}, {"release": "other"},
    {"fingerprint": "changed"}, {"duration_ms": 0}, {"request_status": True},
    {"request_status": 503.0},
    {"ts": None}, {"normalization_origin": None},
])
async def test_stale_sidecar_falls_back_to_canonical_request(updates):
    events = parsed(1)
    model = LocalPartitionModel()
    key = event_key(IDENTITY, events[0].event_id)
    await section_batch(prepare(events), model, model_events={key: replace(events[0], **updates)})
    assert model.payloads[0]["items"][0]["messages"] == [events[0].message]


@pytest.mark.asyncio
async def test_direct_legacy_and_arbitrary_string_sidecars_keep_full_messages():
    events = [replace(event, normalization_origin=None) for event in parsed(2)]
    for candidates in (None, sidecar(events), {key: "replacement" for key in sidecar(events)}):
        model = LocalPartitionModel()
        await section_batch(prepare(events), model, model_events=candidates)
        assert {item["messages"][0] for item in model.payloads[0]["items"]} == {
            event.message for event in events}


@pytest.mark.asyncio
async def test_producer_suffix_rare_fields_and_compatibility_cells_survive():
    events = parse_push_events([
        dict(message='producer\nStructured fields: {"event_id":"producer-777"}',
             event_id=f"event-{i}", id="conflicting-42" if i == 2 else f"event-{i}",
             release="new" if i == 1 else "old", rare_code="RARE-99" if i == 2 else "common",
             nested={"request_id": "req-123"}, timestamp="2026-10-10T00:00:00Z")
        for i in range(3)], preserve_fields=True)
    model = LocalPartitionModel()
    original = prepare(events)
    refined = await section_batch(original, model, model_events=sidecar(events))
    validate_refinement(original, refined)
    messages = [item["messages"][0] for payload in model.payloads for item in payload["items"]]
    assert all('producer-777' in message and 'req-123' in message for message in messages)
    assert any('RARE-99' in message and 'conflicting-42' in message for message in messages)
    assert {chunk.release for chunk in refined.chunks} == {"new", "old"}
