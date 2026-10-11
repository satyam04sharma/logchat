"""Lossless relevance projection checks, with no actual model calls."""
import copy
from dataclasses import replace
import json

import httpx
from test_native_model_support import guarded_transport
import pytest

from pipeline.models import LocalModels
from pipeline.model_transport import model_json
from logchat.rag.record_view import CODEC, project_records, restore_records
from logchat.rag.remapper import _candidate_payload, remap
from logchat.rag.sections import SUMMARY_VERSION, encode_records, records_for_chunk
from test_rag_exact_remapping import CONFIG, Model, candidate, record, retrieval


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def assert_roundtrip(rows):
    original = copy.deepcopy(rows)
    view = project_records(rows)
    # Include the real JSON transport boundary, not only an in-memory inverse.
    assert wire(restore_records(json.loads(wire(view)))) == wire(original)
    assert wire(rows) == wire(original)
    return view


def test_33_records_all_fields_retained_and_smaller():
    rows = [record(str(i), {"ref": "source-ref", "/alias": "literal", "nested": {"雪": [None, True]}})
            for i in range(33)]
    view = assert_roundtrip(rows)
    assert view["codec"] == CODEC
    assert len(view["rows"]) == 33
    assert len(wire(view)) < len(wire(rows))
    assert set(view["keys"]) == set(rows[0])


@pytest.mark.parametrize("values", [[True, 1], [1, 1.0], [-0.0, 0.0],
                                    [{"v": True}, {"v": 1}], [[1], [1.0]]])
def test_typed_values_never_hoisted(values):
    rows = [{"stable": "雪" * 300, "value": value} for value in values]
    view = assert_roundtrip(rows)
    assert "value" in view["columns"]
    assert "value" not in view["shared"]


def test_literal_reserved_names_pointer_aliases_and_key_order():
    row = {"codec": CODEC, "keys": ["source"], "columns": "literal",
           "rows": {"v": None}, "shared": "雪" * 300, "key_orders": True,
           "ref": "source-ref", "/attributes/ref": "source-ref", "null": None}
    rows = [dict(row, event_id="a"), dict(reversed(list(dict(row, event_id="b").items())))]
    view = assert_roundtrip(rows)
    assert view["shared"]["ref"] == "source-ref"
    assert "key_orders" in view


@pytest.mark.parametrize("rows", [[], [{"ref": "e0"}], [{"v": None}, {}],
                                 [{"a": 1}, {"b": 1}], [{"v": 1}, {"v": 2}]])
def test_short_or_heterogeneous_records_stay_ordinary(rows):
    assert isinstance(assert_roundtrip(rows), list)


def test_candidate_metadata_indexes_and_original_binding_unchanged():
    rows = [record(str(i), {"entity": "target" if i == 17 else "neighbor"}) for i in range(33)]
    original = candidate("original", *rows)
    supplied = _candidate_payload(original, "c0", records=rows, matched=[17])
    assert wire(restore_records(supplied["records"])) == wire(rows)
    assert supplied["candidate_ref"] == "c0"
    assert supplied["literal_matching_record_indexes"] == [17]
    assert supplied["literal_matching_record_count"] == 1
    assert supplied["observed_metrics"]["events"] == 33
    assert supplied["observed_metrics"]["scope"] == "whole_section_not_requested_entity"
    assert original.chunk.summary == candidate("original", *rows).chunk.summary


@pytest.mark.asyncio
async def test_semantic_remap_supplies_all_33_records_and_retains_original_citation_candidate():
    rows = [record(str(i), {"entity": "person-" + str(i)}) for i in range(33)]
    original = candidate("original", *rows)
    model = Model()
    summary = 'Observed excerpts:\n33 events [' + ','.join('e' + str(i) for i in range(33)) + ']: "Session renewal rejected"'
    original = replace(original, chunk=replace(original.chunk, summary=summary,
        compression_version=SUMMARY_VERSION, supporting_records=encode_records(rows), summary_model="mock"))
    checked = await remap(retrieval("Describe session renewal failures", original), model,
                          config=CONFIG, preserve_content=True)
    supplied = model.calls[0][1]["candidate"]
    restored = restore_records(supplied["records"])
    assert len(restored) == 33
    # The pre-existing source view omits the derived event_key, and preserves
    # source event IDs and every remaining field; citation uses the original.
    retained = records_for_chunk(original.chunk)
    assert wire(restored) == wire([{k: v for k, v in row.items() if k != "event_key"} for row in retained])
    assert checked.selected[0].candidate is original


@pytest.mark.asyncio
async def test_compact_relevance_budget_matches_native_submitted_prompt_and_schema():
    rows = [record(str(i), {"nested": {"雪": [True, 1, 1.0, -0.0, None]},
                           "ref": "literal-source-ref"}) for i in range(33)]
    original = candidate("original", *rows)
    # A summary-backed candidate keeps the existing summary size bound intact.
    summary = 'Observed excerpts:\n33 events [' + ','.join('e' + str(i) for i in range(33)) + ']: "Session renewal rejected"'
    original = replace(original, chunk=replace(original.chunk, summary=summary,
        compression_version=SUMMARY_VERSION, supporting_records=encode_records(rows), summary_model="mock"))
    requests = []

    def handler(request):
        assert request.url.path == "/api/generate"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"response": json.dumps({"label": "topic_match", "confidence": .9}), "done_reason": "stop"})

    model = LocalModels(base_url="http://127.0.0.1:11434", preserve_content=True,
                        transport=guarded_transport(handler))
    query = retrieval("Describe session renewal failures", original)
    checked = await remap(query, model, config=CONFIG, preserve_content=True)
    assert checked.selected[0].candidate is original
    body = requests[0]
    payload = json.loads(body["prompt"])
    assert body["prompt"] == model_json(payload)
    assert len(body["prompt"].encode()) < len(json.dumps(payload, ensure_ascii=False).encode())
    restored = restore_records(payload["candidate"]["records"])
    assert wire(restored) == wire([{k: v for k, v in row.items() if k != "event_key"}
                                  for row in records_for_chunk(original.chunk)])
    from logchat.rag.remapper import CLASSIFICATION_INSTRUCTION, SYSTEM_SAFETY_SUFFIX
    # Full instructions, appended safety, schema, and payload are charged.
    measured = CLASSIFICATION_INSTRUCTION + SYSTEM_SAFETY_SUFFIX + model_json(body["format"]) + body["prompt"]
    detail = json.loads(checked.provenance[0].detail)
    assert detail["input_characters"] == len(measured)
    assert detail["input_utf8_bytes"] == len(measured.encode())
    exact = replace(CONFIG, input_character_budget=len(measured), input_byte_budget=len(measured.encode()))
    assert (await remap(query, model, config=exact, preserve_content=True)).selected
    assert len(requests) == 2
    for config in (replace(exact, input_character_budget=len(measured) - 1),
                   replace(exact, input_byte_budget=len(measured.encode()) - 1)):
        rejected = await remap(query, model, config=config, preserve_content=True)
        assert not rejected.selected
        assert "current:remapper_input_budget_reached" in rejected.gaps
    assert len(requests) == 2
