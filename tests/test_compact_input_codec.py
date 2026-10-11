"""Offline reversible input tests; no provider, counter, or native assets."""
from copy import deepcopy
import json

import pytest

from logchat.rag.compact_input import (
    COMPACT_INPUT_VERSION, expand_compact_input, project_compact_input,
    validate_compact_input_projection,
)


def complete_input():
    messages = ['Repeated authentication message "quoted"\n雪' * 20,
                'rare quote: expired token; request ID req-73']
    return {
        "records": [{"ref": f"e{i}", "message": messages[i == 2],
                     "timestamp": f"2026-10-10T00:00:00.{i:03d}+00:00",
                     "request_status": 401 if i == 2 else 200,
                     "duration_ms": [None, -0.0, 1.0, 1][i],
                     "future_field": {"nested": [False, None, 9007199254740993]}}
                    for i in range(4)],
        "field_catalog": [
            {"field_ref": f"f{i}", "key": key, "value": value, "event_refs": refs}
            for i, (key, value, refs) in enumerate([
                ("request_id", "req-73", ["e2"]),
                ("optional", False, ["e0", "e3"]),
                ("optional", 0, ["e1"]),
                ("optional", 0.0, ["e2"]),
                ("nested", [None, {"value": 9007199254740993}], ["e0"]),
            ])],
        "required_field_refs": ["f0"],
        "phrase_catalog": [["p0", "expired token"], ["p1", "token"],
                           ["p2", "request ID req-73"]],
        "catalog_omissions": [],
        "context": {"service": "auth"},
        "exact_metrics": {"status_counts": {"200": 3, "401": 1}},
    }


def test_roundtrip_is_typed_complete_deterministic_and_independent():
    original = complete_input()
    before = deepcopy(original)
    projected = project_compact_input(original)
    assert original == before
    assert projected["input_projection"] == COMPACT_INPUT_VERSION
    assert list(projected["message_dictionary"]) == ["m0", "m1"]
    assert [row["message_ref"] for row in projected["records"]] == ["m0", "m0", "m1", "m0"]
    assert all("message" not in row for row in projected["records"])
    assert len(json.dumps(projected).encode()) < len(json.dumps(original).encode())
    saved = json.loads(json.dumps(projected))
    validate_compact_input_projection(original, saved)
    expanded = expand_compact_input(saved)
    assert json.dumps(expanded, sort_keys=True) == json.dumps(original, sort_keys=True)
    assert project_compact_input(expanded) == projected
    expanded["field_catalog"][0]["event_refs"].append("e9")
    projected["records"][0]["future_field"]["nested"].append("changed")
    assert original == before
    assert saved["field_catalog"] == before["field_catalog"]


@pytest.mark.parametrize("message", ["", "é", "e\u0301", " a ", "a\n", "a\r\n"])
def test_exact_strings_are_never_normalized(message):
    original = {"records": [{"ref": "e0", "message": message},
                            {"ref": "e1", "message": message + " "}]}
    projected = project_compact_input(original)
    assert len(projected["message_dictionary"]) == 2
    validate_compact_input_projection(original, projected)


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("input_projection"),
    lambda p: p.update(input_projection="compact-message-dictionary-v2"),
    lambda p: p.update(input_projection=True),
    lambda p: p.update(message_dictionary=[]),
    lambda p: p["message_dictionary"].update(m2="unused"),
    lambda p: p["message_dictionary"].update(m2=p["message_dictionary"]["m0"]),
    lambda p: p["message_dictionary"].update(m0=None),
    lambda p: p["records"][0].update(message_ref="missing"),
    lambda p: p["records"][0].update(message_ref=0),
    lambda p: p["records"][0].update(message="ambiguous"),
    lambda p: p["records"][0].update(ref="e1"),
    lambda p: p["records"][0].update(ref=False),
    lambda p: p["records"][0].pop("message_ref"),
    lambda p: p.update(records={}),
])
def test_malformed_saved_projections_fail_without_body_diagnostics(mutate):
    projected = project_compact_input(complete_input())
    mutate(projected)
    with pytest.raises(ValueError, match="^compact_input_projection_invalid$"):
        expand_compact_input(projected)


def test_relabelled_dictionary_is_not_a_valid_v1_encoding():
    projected = project_compact_input(complete_input())
    projected["message_dictionary"]["alias"] = projected["message_dictionary"].pop("m0")
    for row in projected["records"]:
        if row["message_ref"] == "m0":
            row["message_ref"] = "alias"
    with pytest.raises(ValueError, match="projection_invalid"):
        expand_compact_input(projected)


@pytest.mark.parametrize("mutate", [
    lambda p: p["records"][3].update(message_ref="m1"),
    lambda p: p["records"].pop(),
    lambda p: p["records"].reverse(),
    lambda p: p["records"][0].update(ref="replacement"),
    lambda p: p["records"][0].update(duration_ms=0),
    lambda p: p["records"][0].update(request_status=200.0),
    lambda p: p["records"][1].update(duration_ms=0.0),
    lambda p: p["records"][2].update(duration_ms=1),
    lambda p: p["field_catalog"][1].update(value=0),
    lambda p: p["field_catalog"][2].update(value=0.0),
    lambda p: p["field_catalog"][0]["event_refs"].append("e1"),
    lambda p: p["field_catalog"].pop(),
    lambda p: p["phrase_catalog"].pop(1),
    lambda p: p["phrase_catalog"][0].__setitem__(1, "token"),
    lambda p: p["required_field_refs"].clear(),
])
def test_complete_equality_rejects_valid_but_changed_inputs(mutate):
    original = complete_input()
    projected = project_compact_input(original)
    mutate(projected)
    with pytest.raises(ValueError, match="compact_input_projection_(invalid|mismatch)"):
        validate_compact_input_projection(original, projected)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), {1: "coercion"}])
def test_non_json_or_coerced_values_reject(value):
    original = complete_input()
    original["extra"] = value
    with pytest.raises(ValueError, match="projection_invalid"):
        project_compact_input(original)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(input_projection=COMPACT_INPUT_VERSION),
    lambda p: p.update(message_dictionary={}),
    lambda p: p["records"][0].update(message_ref="m0"),
    lambda p: p["records"][0].update(message=False),
    lambda p: p["records"].append(deepcopy(p["records"][0])),
])
def test_ambiguous_originals_reject(mutate):
    original = complete_input()
    mutate(original)
    with pytest.raises(ValueError, match="projection_invalid"):
        project_compact_input(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", [False, True])
async def test_actual_complete_compact_request_conserves_every_choice(catalog):
    from logchat.rag.sections import SummaryConfig, _expanded_compact_request, records_for_chunk
    from tests.test_compact_message_projection import fixture, catalog_request_options, value
    from tests.test_grouping_projection import sidecar

    events, batch = await fixture([value(i, optional_choice=False if i % 2 else 0,
                                      rare_code="RARE-73") for i in range(4)])
    messages, options = catalog_request_options(batch, sidecar(events))
    for chunk in batch.chunks:
        original, schema, metadata, fields = _expanded_compact_request(
            chunk, records_for_chunk(chunk), SummaryConfig(), messages,
            **(options if catalog else {}))
        before = deepcopy((original, schema, metadata, fields))
        projected = project_compact_input(original)
        validate_compact_input_projection(original, projected)
        assert expand_compact_input(projected) == original
        assert (original, schema, metadata, fields) == before
        for key in set(original) - {"records"}:
            assert projected[key] == original[key]
