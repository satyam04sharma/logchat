"""Trusted compact views preserve canonical fields and exclude producer-body echoes."""
from collections import Counter
from dataclasses import asdict, replace

import pytest

from connectors.local import normalized_model_message, parse_push_events
from logchat.rag.sections import (SectionModelError, compact_records_for_chunk,
    compact_summary_batch, records_for_chunk, section_batch, validate_refinement)
from tests.test_grouping_projection import prepare, sidecar
from tests.test_rag_compact import CompactModel
from tests.test_rag_sections import LocalPartitionModel


async def fixture(values):
    events = parse_push_events(values, preserve_fields=True)
    original = await section_batch(prepare(events), LocalPartitionModel(), model_events=sidecar(events))
    return events, original


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
@pytest.mark.parametrize("destination", ["summary", "uncertainties"])
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
            if length == 100:
                text = "Model prefix " + text + " suffix"
            result[destination] = text if destination == "summary" else [text]
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
    assert payloads[0]["records"] == payloads[1]["records"]
    assert payloads[0]["field_catalog"] != payloads[1]["field_catalog"]
    assert payloads[0]["exact_metrics"] != payloads[1]["exact_metrics"]


@pytest.mark.asyncio
async def test_projection_does_not_rescue_oversized_mandatory_single():
    events, original = await fixture([value(0), value(1, **{f"resource_{i}_id": i for i in range(65)})])
    model = CompactModel("empty_fields")
    with pytest.raises(SectionModelError, match="compact_input_budget_exceeded"):
        await compact_summary_batch(original, model, model_events=sidecar(events))
    assert model.calls == 0
