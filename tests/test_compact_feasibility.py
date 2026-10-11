"""Canonical compact packing contracts; fixture models only."""
from collections import Counter
from dataclasses import asdict

import pytest

from connectors.local import parse_push_events
from logchat.rag.sections import (SectionModelError, SummaryConfig, _compact_feasible,
    compact_records_for_chunk, compact_summary_batch, records_for_chunk, section_batch,
    validate_refinement)
from tests.test_grouping_projection import prepare, sidecar
from tests.test_rag_compact import CompactModel
from tests.test_rag_sections import LocalPartitionModel


async def sections(values):
    events = parse_push_events(values, preserve_fields=True)
    return await section_batch(prepare(events), LocalPartitionModel(), model_events=sidecar(events))


def value(index, **extra):
    return dict(event_id=f"event-{index}", id=f"event-{index}",
        message="PRIVATE_BODY operation failed", timestamp="2026-10-10T00:00:00Z",
        duration_ms=index + .5, request_status=504 if index == 32 else 200, **extra)


@pytest.mark.asyncio
async def test_dual_alias_64_boundary_exact_records_metrics_and_current_requests():
    original = await sections([value(i) for i in range(33)])
    assert len(original.chunks) == 1
    model = CompactModel("empty_fields")
    compact = await compact_summary_batch(original, model)
    assert sorted(chunk.metrics.event_count for chunk in compact.chunks) == [1, 32]
    assert sorted(len(payload["field_catalog"]) for payload in model.payloads) == [2, 64]
    rows = [row for chunk in compact.chunks for row in compact_records_for_chunk(chunk)]
    assert Counter(row["event_id"] for row in rows) == Counter(f"event-{i}" for i in range(33))
    for row in rows:
        assert {item["key"]: item["value"] for item in row["important_fields"]} == {
            "event_id": row["event_id"], "id": row["event_id"]}
    for chunk, payload in zip(compact.chunks, model.payloads):
        assert payload["exact_metrics"] == asdict(chunk.metrics)
        assert all(len(field["event_refs"]) == 1 for field in payload["field_catalog"])
    assert "PRIVATE_BODY" not in str(asdict(compact))
    validate_refinement(original, compact)


@pytest.mark.asyncio
async def test_typed_aliases_preserved_and_oversized_late_single_preflights_entire_batch():
    original = await sections([value(0), value(1, **{f"resource_{i}_id": i for i in range(65)})])
    model = CompactModel("empty_fields")
    with pytest.raises(SectionModelError, match="compact_input_budget_exceeded"):
        await compact_summary_batch(original, model)
    assert model.calls == 0
    original = await sections([dict(value(0), id=False), dict(value(1), id=7.0)])
    compact = await compact_summary_batch(original, CompactModel("empty_fields"))
    rows = [row for chunk in compact.chunks for row in compact_records_for_chunk(chunk)]
    aliases = {row["event_id"]: next(field["value"] for field in row["important_fields"] if field["key"] == "id") for row in rows}
    assert aliases["event-0"] is False
    assert type(aliases["event-1"]) is float and aliases["event-1"] == 7.0
    validate_refinement(original, compact)


@pytest.mark.asyncio
async def test_request_bounds_split_with_complete_source_messages():
    from dataclasses import replace
    from unittest.mock import patch
    from logchat.rag import sections as section_api
    from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json

    original = await sections([value(i) for i in range(8)])
    assert len(original.chunks) == 1
    # Capture the production catalog binding with a pure mock at default bounds.
    # Measure the projected wire payload and exact schema, not expanded records.
    control = CompactModel("empty_fields")
    with patch.object(section_api, "_compact_request", wraps=section_api._compact_request) as factory:
        await compact_summary_batch(original, control)
    assert control.calls == 1
    (chunk, records, default_config, messages), options = factory.call_args
    assert len(records) == 8

    def request_chars(candidate, rows):
        payload, schema, _, _ = section_api._compact_request(
            candidate, rows, default_config, messages, **options)
        return len(section_api.CATALOG_COMPACT_INSTRUCTION + SYSTEM_SAFETY_SUFFIX
                   + model_json(payload) + model_json(schema))

    singletons = [section_api.make_chunk(original.batch_id, original.identity,
        chunk.window, [row], section_api.SECTION_VERSION) for row in records]
    # The midpoint of the measured singleton maximum and full eight-record request
    # leaves room for singletons but still requires splitting.
    single = max(request_chars(child, [row]) for child, row in zip(singletons, records))
    config = SummaryConfig(input_character_budget=(single + request_chars(chunk, records)) // 2)
    assert max(request_chars(child, [row]) for child, row in zip(singletons, records)) <= config.input_character_budget
    assert config.input_character_budget < request_chars(chunk, records)
    for child, row in zip(singletons, records):
        _compact_feasible(child, [row], config, messages, **options)
    with pytest.raises(SectionModelError, match="compact_input_budget_exceeded"):
        _compact_feasible(chunk, records, config, messages, **options)

    model = CompactModel("empty_fields")
    compact = await compact_summary_batch(original, model, config=config)
    assert len(model.payloads) > 1
    assert Counter(row["message"] for payload in model.payloads for row in payload["records"]) == Counter(
        row["message"] for chunk in original.chunks for row in records_for_chunk(chunk))
    assert [row["message"] for payload in model.payloads for row in payload["records"]] == [
        row["message"] for row in records]
    assert [row["event_key"] for child in compact.chunks for row in compact_records_for_chunk(child)] == [
        row["event_key"] for row in records]
    validate_refinement(original, compact)
    # Oversized evidence is rejected independently of a generous request budget.
    chunk = original.chunks[0]
    with pytest.raises(SectionModelError, match="compact_input_budget_exceeded"):
        _compact_feasible(chunk, [dict(records_for_chunk(chunk)[0], source="s" * 16000)],
                          replace(config, input_character_budget=32000))
