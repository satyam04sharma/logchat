"""Trusted compact views preserve canonical fields and exclude producer-body echoes."""
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime

import pytest

from connectors.local import normalized_model_message, parse_push_events
from logchat.local.raw_capture import content_digest
from logchat.rag.sections import (CATALOG_COMPACT_INSTRUCTION, SectionModelError, SummaryConfig,
    _compact_feasible, _compact_request, _json, compact_records_for_chunk,
    compact_summary_batch, records_for_chunk, section_batch, validate_refinement)
from logchat.rag.source_catalog import build_source_catalog
from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json
from connectors.local import validated_normalization_origin
from logchat.rag.sections import _source_fields
from tests.test_grouping_projection import prepare, sidecar
from tests.test_rag_compact import CompactModel
from tests.test_rag_sections import LocalPartitionModel


async def fixture(values):
    events = parse_push_events(values, preserve_fields=True)
    original = await section_batch(prepare(events), LocalPartitionModel(), model_events=sidecar(events))
    return events, original


def catalog_request_options(batch, candidates):
    """Reconstruct the complete authenticated global sidecar for equality checks."""
    rows = [row for chunk in batch.chunks for row in records_for_chunk(chunk)]
    messages = {row["event_key"]: normalized_model_message(candidates[row["event_key"]])
                if candidates else row["message"] for row in rows}
    bodies = {row["event_key"]: {row["message"], messages[row["event_key"]]} for row in rows}
    for row in rows:
        event = candidates[row["event_key"]] if candidates else None
        origin = validated_normalization_origin(event) if event else None
        if origin is not None:
            bodies[row["event_key"]].add(event.message[:origin.prefix_chars])
    typed_values = {_json(value) for row in rows for value in _source_fields(row).values()
                    if not isinstance(value, dict)}
    return messages, dict(source_catalog=build_source_catalog(messages, bodies, typed_values=typed_values),
                          protected_bodies=bodies)


def value(index, **extra):
    return dict(event_id=f"event-{index}", id=f"event-{index}",
        message='Producer operation 9007199254740993 failed\nStructured fields: {"request_id":"producer-73"}',
        timestamp="2026-10-10T00:00:00Z", duration_ms=index + .5,
        request_status=504 if index == 32 else 200, **extra)


@pytest.mark.asyncio
async def test_projected_packing_keeps_aliases_types_current_refs_and_exact_metrics():
    events, original = await fixture([value(i, rare_code="FAIL-73") for i in range(33)])
    model = CompactModel("empty_fields")
    compact = await compact_summary_batch(original, model, model_events=sidecar(events))
    assert len(compact.chunks) == 2  # 64 mandatory fields, including both original aliases
    assert Counter(row["message"] for payload in model.payloads for row in payload["records"]) == Counter(
        normalized_model_message(event) for event in events)
    for payload, chunk in zip(model.payloads, compact.chunks):
        assert payload["exact_metrics"] == asdict(chunk.metrics)
        rows = compact_records_for_chunk(chunk)
        for row in rows:
            fields = {field["key"]: field["value"] for field in row["important_fields"]}
            assert fields["event_id"] == fields["id"] == row["event_id"]
            assert fields["request_id"] == "producer-73"
            assert fields["rare_code"] == "FAIL-73"
    assert "Producer operation" not in str(asdict(compact))
    validate_refinement(original, compact)


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"duration_ms": 0}, {"duration_ms": False}, {"request_status": 200.0},
    {"message": "different"}, {"event_id": "other"}, {"source": "other"},
    {"ts": None}, {"normalization_origin": None},
])
async def test_compact_stale_sidecars_fall_back(updates):
    events, original = await fixture([value(0)])
    candidates = {key: replace(event, **updates) for key, event in sidecar(events).items()}
    model = CompactModel("empty_fields")
    await compact_summary_batch(original, model, model_events=candidates)
    assert model.payloads[0]["records"][0]["message"] == events[0].message


@pytest.mark.asyncio
async def test_direct_and_string_sidecars_preserve_canonical_and_typed_aliases():
    events, original = await fixture([dict(value(0), id=False), dict(value(1), id=7.0)])
    for candidates in (None, {key: "fake" for key in sidecar(events)}, sidecar(events)):
        model = CompactModel("empty_fields")
        compact = await compact_summary_batch(original, model, model_events=candidates)
        expected = [normalized_model_message(event) if candidates == sidecar(events) else event.message for event in events]
        assert Counter(row["message"] for payload in model.payloads for row in payload["records"]) == Counter(expected)
        rows = [row for chunk in compact.chunks for row in compact_records_for_chunk(chunk)]
        aliases = {row["event_id"]: next(field["value"] for field in row["important_fields"] if field["key"] == "id") for row in rows}
        assert aliases["event-0"] is False
        assert type(aliases["event-1"]) is float and aliases["event-1"] == 7.0
        validate_refinement(original, compact)


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [20, 100])
@pytest.mark.parametrize("destination", ["source_phrases", "summary", "uncertainties"])
@pytest.mark.parametrize("echo", ["producer", "canonical", "projection"])
async def test_full_body_echoes_reject(length, destination, echo):
    body = "PRIVATE_BODY " + "x" * length
    events, original = await fixture([dict(value(0), message=body, extra="retained")])
    class Echo(CompactModel):
        async def generate(self, instruction, payload, schema):
            result = await super().generate(instruction, payload, schema)
            text = {"producer": body, "canonical": events[0].message,
                    "projection": normalized_model_message(events[0])}[echo]
            # Preserve the existing >=80 character containment rule as well.
            if length == 100 and destination != "source_phrases":
                text = "Model prefix " + text + " suffix"
            result[destination] = ([{"record_ref": "e0", "text": text}] if destination == "source_phrases"
                                   else text if destination == "summary" else [text])
            return result
    with pytest.raises(SectionModelError, match="compact_invalid_output"):
        await compact_summary_batch(original, Echo(), model_events=sidecar(events))


@pytest.mark.asyncio
async def test_independent_requests_never_reuse_prior_attribution_or_metrics():
    model = CompactModel("empty_fields")
    payloads = []
    for offset in (0, 50):
        events, original = await fixture([value(offset, rare_code="FAIL-73")])
        compact = await compact_summary_batch(original, model, model_events=sidecar(events))
        payloads.append(model.payloads[-1])
        row = compact_records_for_chunk(compact.chunks[0])[0]
        assert row["event_id"] == events[0].event_id
        assert row["duration_ms"] == events[0].duration_ms
        validate_refinement(original, compact)
    assert model.calls == 2
    assert payloads[0]["records"][0]["message"] == payloads[1]["records"][0]["message"]
    assert payloads[0]["records"][0]["duration_ms"] == .5
    assert payloads[1]["records"][0]["duration_ms"] == 50.5
    assert payloads[0]["field_catalog"] != payloads[1]["field_catalog"]
    assert payloads[0]["exact_metrics"] != payloads[1]["exact_metrics"]


@pytest.mark.asyncio
@pytest.mark.parametrize("projected", [False, True])
async def test_mixed_status_section_keeps_each_message_measurement_binding(projected):
    values = [dict(event_id=f"event-{i}",
        message="Payment incident reported" if i == 0 else "Directory index forbidden",
        timestamp=f"2026-10-10T00:00:00.{i:03d}Z",
        duration_ms=5000 if i == 0 else i + .5,
        request_status=504 if i == 0 else 200) for i in range(33)]
    events, original = await fixture(values)
    digests = [content_digest(event) for event in events]
    assert len(original.chunks) == 1
    chunk = original.chunks[0]
    records = records_for_chunk(chunk)
    candidates = sidecar(events) if projected else None
    model = CompactModel("empty_fields")
    compact = await compact_summary_batch(original, model, model_events=candidates)
    assert model.calls == 1 and len(compact.chunks) == 1
    payload = model.payloads[0]
    assert len(payload["records"]) == 33
    assert payload["exact_metrics"]["status_counts"] == {"200": 32, "504": 1}
    for supplied, canonical, stored in zip(payload["records"], records,
            compact_records_for_chunk(compact.chunks[0])):
        assert set(supplied) == {"ref", "message", "request_status", "duration_ms", "timestamp"}
        for key in ("ref", "request_status", "duration_ms", "timestamp"):
            assert supplied[key] == stored[key]
        for key in ("request_status", "duration_ms", "timestamp"):
            assert supplied[key] == canonical[key]
        if "Payment incident" in supplied["message"]:
            assert supplied["request_status"] == 504 and supplied["duration_ms"] == 5000
        else:
            assert "Directory index forbidden" in supplied["message"]
            assert supplied["request_status"] == 200
    # Generation and preflight use the same complete bounded request.
    config = SummaryConfig()
    messages, options = catalog_request_options(original, candidates)
    expected, schema, _, _ = _compact_request(chunk, records, config, messages, **options)
    assert payload == expected
    _compact_feasible(chunk, records, config, messages, **options)
    serialized = CATALOG_COMPACT_INSTRUCTION + SYSTEM_SAFETY_SUFFIX + model_json(payload) + model_json(schema)
    assert len(serialized) <= config.input_character_budget
    assert len(serialized.encode()) <= config.input_byte_budget
    assert "complete ordered exact substring membership" in CATALOG_COMPACT_INSTRUCTION
    assert "not timestamp span, adjacency, cause or recovery" in CATALOG_COMPACT_INSTRUCTION
    assert [content_digest(event) for event in events] == digests
    assert compact.coverage == original.coverage
    assert compact.event_keys == original.event_keys
    validate_refinement(original, compact)


@pytest.mark.asyncio
async def test_missing_measurements_remain_null_in_compact_input():
    _, original = await fixture([dict(value(0), request_status=None, duration_ms=None)])
    model = CompactModel("empty_fields")
    await compact_summary_batch(original, model)
    supplied = model.payloads[0]["records"][0]
    assert supplied["request_status"] is None and supplied["duration_ms"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("projected", [False, True])
async def test_separated_auth_runs_keep_timestamps_span_and_work_sum_distinct(projected):
    # Two 15-event runs with 24 other source events between them. This tests
    # request fidelity, not model prose or a claim that timestamps prove adjacency.
    values = [dict(event_id=f"auth-{i}", message="Authentication request rejected",
        service="auth", timestamp=f"2026-10-10T00:00:00.{i:03d}Z",
        duration_ms=48 if i == 0 else 51, request_status=401)
        for i in (*range(15), *range(39, 54))]
    intervening = [dict(event_id=f"other-{i}", message="Directory request completed",
        service="web", timestamp=f"2026-10-10T00:00:00.{i:03d}Z",
        duration_ms=1, request_status=200) for i in range(15, 39)]
    events, original = await fixture(values + intervening)
    chunk = next(chunk for chunk in original.chunks if chunk.service == "auth")
    assert chunk.metrics.event_count == 30
    records = records_for_chunk(chunk)
    candidates = sidecar(events) if projected else None
    messages, options = catalog_request_options(original, candidates)
    config = SummaryConfig()
    expected, schema, _, _ = _compact_request(chunk, records, config, messages, **options)
    _compact_feasible(chunk, records, config, messages, **options)
    model = CompactModel("empty_fields")
    digests = [content_digest(event) for event in events]
    compact = await compact_summary_batch(original, model, model_events=candidates)
    payload = next(payload for payload in model.payloads if payload["context"]["service"] == "auth")
    assert payload == expected
    assert payload["exact_metrics"]["duration_sum_ms"] == 1527
    assert payload["exact_metrics"]["status_counts"] == {"401": 30}
    assert payload["observed_window"]["span_ms"] == 53
    timestamps = sorted(datetime.fromisoformat(row["timestamp"]) for row in payload["records"])
    gaps = [(right - left).total_seconds() * 1000 for left, right in zip(timestamps, timestamps[1:])]
    assert gaps == [1] * 14 + [25] + [1] * 14
    assert len(intervening) == 24
    stored = compact_records_for_chunk(next(chunk for chunk in compact.chunks if chunk.service == "auth"))
    assert len(stored) == len(records) == len(payload["records"]) == 30
    for supplied, canonical, retained in zip(payload["records"], records, stored):
        assert set(supplied) == {"ref", "message", "request_status", "duration_ms", "timestamp"}
        for key in ("ref", "request_status", "duration_ms", "timestamp"):
            assert supplied[key] == retained[key]
        for key in ("request_status", "duration_ms", "timestamp"):
            assert supplied[key] == canonical[key]
    assert "consecutive" not in _json(payload) and "adjacency" not in _json(payload)
    assert "not timestamp span, adjacency, cause or recovery" in CATALOG_COMPACT_INSTRUCTION
    assert "NOT elapsed wall-clock time" in payload["metric_semantics"]["duration_sum_ms"]
    serialized = CATALOG_COMPACT_INSTRUCTION + SYSTEM_SAFETY_SUFFIX + model_json(payload) + model_json(schema)
    assert len(serialized) <= config.input_character_budget
    assert len(serialized.encode()) <= config.input_byte_budget
    assert [content_digest(event) for event in events] == digests
    assert compact.coverage == original.coverage and compact.event_keys == original.event_keys
    validate_refinement(original, compact)


@pytest.mark.asyncio
async def test_projection_does_not_rescue_oversized_mandatory_single():
    events, original = await fixture([value(0), value(1, **{f"resource_{i}_id": i for i in range(65)})])
    model = CompactModel("empty_fields")
    with pytest.raises(SectionModelError, match="compact_input_budget_exceeded"):
        await compact_summary_batch(original, model, model_events=sidecar(events))
    assert model.calls == 0
