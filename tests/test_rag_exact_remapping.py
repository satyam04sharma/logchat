"""Generic exact-field constraints, using mocked relevance decisions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from logchat.rag.contracts import Candidate, EmbeddingSpec, RetrievalCell, RetrievalResult, SemanticChunk, SourceIdentity, TimeWindow
from logchat.rag.builder import event_key
from logchat.rag.remapper import RemapConfig, remap
from logchat.rag.sections import RECORD_PREFIX, encode_records, record_metrics

START = datetime(2026, 10, 4, tzinfo=timezone.utc)
WINDOW = TimeWindow(START, START + timedelta(hours=1))
SCOPE = SourceIdentity("owner", "project", "dev", "source")
CELL = RetrievalCell("current", "owner", "project", "dev", WINDOW, source_ids=("source",))
SPEC = EmbeddingSpec("mock", "fake", "unit-only", 3)
CONFIG = RemapConfig(input_character_budget=24000, input_byte_budget=48000)


def record(identifier, values, *, message="Session renewal rejected", level="error"):
    return {"event_key": event_key(SCOPE, identifier), "event_id": identifier,
            "timestamp": START.isoformat(), "source": "source", "service": "gateway", "level": level,
            "release": "release-1", "fingerprint": "original-fingerprint-" + identifier,
            "message": message + "\nStructured fields: " + json.dumps(values),
            "duration_ms": 42, "request_status": 409}


def candidate(identifier, *records):
    value = SemanticChunk(identifier, SCOPE, WINDOW, "gateway", records[0]["level"],
                          encode_records(records), record_metrics(records), "pattern-" + identifier,
                          release="release-1", first_event_at=START, last_event_at=START,
                          compression_version="local-model-section-v1")
    return Candidate("current", value, .03, .8, 1)


def retrieval(question, *candidates):
    return RetrievalResult(question, (CELL,), tuple(candidates), (), SPEC)


class Model:
    chat_model = "mock-exact-value-relevance"

    def __init__(self, result=None):
        self.result, self.calls = result, []

    async def generate(self, instruction, payload, schema):
        self.calls.append((instruction, payload, schema))
        if self.result is not None:
            return self.result
        return {"selections": [{"evidence_ref": item["evidence_ref"], "reason": "topic_match", "confidence": .9}
                               for cell in payload["cells"] for item in cell["candidates"]]}


class ExactValueTests(unittest.IsolatedAsyncioTestCase):
    async def test_phone_constraint_removes_neighbor_before_model_ids_assigned(self):
        alice = candidate("alice", record("event-a", {"phone": "+1-202-555-0101"}))
        bob = candidate("bob", record("event-b", {"phone": "+1-202-555-0102"}))
        model = Model()
        checked = await remap(retrieval("What happened with phone +1-202-555-0102?", alice, bob),
                              model, config=CONFIG, preserve_content=True)
        self.assertEqual([item.candidate.chunk.chunk_id for item in checked.selected], ["bob"])
        supplied = model.calls[0][1]["cells"][0]["candidates"]
        self.assertEqual(len(supplied), 1)
        self.assertEqual(supplied[0]["evidence_ref"], "e0")
        self.assertEqual(model.calls[0][2]["properties"]["selections"]["items"]["properties"]["evidence_ref"]["enum"], ["e0"])
        self.assertEqual(json.loads(checked.provenance[0].detail)["literal_nonmatching_candidates"], 1)

    async def test_readable_records_replace_framed_summary_and_derived_hashes(self):
        row = record("event-a", {"customer": "River Birch"})
        item = candidate("derived-chunk-hash", row)
        model = Model()
        await remap(retrieval("Why was authentication rejected?", item), model, config=CONFIG, preserve_content=True)
        payload = model.calls[0][1]["cells"][0]["candidates"][0]
        self.assertNotIn("template", payload)
        self.assertNotIn("chunk_id", payload)
        self.assertNotIn(RECORD_PREFIX, json.dumps(payload))
        self.assertNotIn(row["event_key"], json.dumps(payload))
        self.assertEqual(payload["records"], [{key: value for key, value in row.items() if key != "event_key"}])
        self.assertEqual(payload["observed_metrics"]["scope"], "whole_section_not_requested_entity")

    async def test_arbitrary_nested_original_fields_are_constraints_without_entity_regex(self):
        a = candidate("other", record("event-a", {"arbitrary": {"rack_tag": "BRANCH.blue/72"}}))
        b = candidate("wanted", record("event-b", {"arbitrary": {"rack_tag": "BRANCH.blue/73"}}))
        checked = await remap(retrieval("Inspect BRANCH.blue/73.", a, b), Model(), config=CONFIG, preserve_content=True)
        self.assertEqual([item.candidate.chunk.chunk_id for item in checked.selected], ["wanted"])

    async def test_short_identifier_is_not_a_prefix_match(self):
        short = candidate("short", record("event-a", {"custom_value": "SDK-42"}))
        full = candidate("full", record("event-b", {"custom_value": "SDK-420"}))
        checked = await remap(retrieval("Investigate SDK-420", short, full), Model(), config=CONFIG, preserve_content=True)
        self.assertEqual([item.candidate.chunk.chunk_id for item in checked.selected], ["full"])
        self.assertEqual(json.loads(checked.provenance[0].detail)["literal_anchor_values"], 1)

    async def test_values_across_distinct_fields_must_cooccur_in_one_record(self):
        mixed = candidate("cross-record", record("event-a", {"account": "River Birch", "code": "STORE-7"}),
                          record("event-b", {"account": "Cedar Oak", "code": "AUTH-9"}))
        wanted = candidate("cooccurs", record("event-c", {"account": "River Birch", "code": "AUTH-9"}))
        checked = await remap(retrieval("Explain River Birch and AUTH-9.", mixed, wanted), Model(), config=CONFIG, preserve_content=True)
        self.assertEqual([item.candidate.chunk.chunk_id for item in checked.selected], ["cooccurs"])

    async def test_comparison_alternatives_within_same_field_are_not_an_impossible_and(self):
        first = candidate("first", record("event-a", {"account": "River Birch"}))
        second = candidate("second", record("event-b", {"account": "Cedar Oak"}))
        checked = await remap(retrieval("Compare River Birch with Cedar Oak", first, second), Model(), config=CONFIG, preserve_content=True)
        self.assertEqual(len(checked.selected), 2)

    async def test_no_cooccurrence_yields_unknown_without_model_call_or_fallback(self):
        mixed = candidate("cross-record", record("event-a", {"account": "River Birch", "code": "STORE-7"}),
                          record("event-b", {"account": "Cedar Oak", "code": "AUTH-9"}))
        model = Model()
        checked = await remap(retrieval("Explain River Birch and AUTH-9.", mixed), model, config=CONFIG, preserve_content=True)
        self.assertFalse(checked.selected)
        self.assertFalse(model.calls)
        self.assertIn("no_candidate_record_contains_requested_literal_values", checked.gaps)

    async def test_paraphrase_and_generic_metadata_do_not_force_identity_filters(self):
        failure = candidate("failure", record("event-a", {"account": "River Birch"}))
        baseline = candidate("baseline", record("event-b", {"account": "Cedar Oak"},
                                                message="Session renewal succeeded", level="info"))
        for question in ("Why did visitors need to authenticate again?", "Compare gateway error behavior with normal sessions"):
            model = Model()
            checked = await remap(retrieval(question, failure, baseline), model, config=CONFIG, preserve_content=True)
            self.assertEqual(len(model.calls[0][1]["cells"][0]["candidates"]), 2)
            self.assertEqual(json.loads(checked.provenance[0].detail)["literal_anchor_fields"], 0)

    async def test_matching_subset_does_not_rewrite_immutable_section_metrics(self):
        mixed = candidate("mixed", record("event-a", {"account": "River Birch"}),
                          record("event-b", {"account": "Cedar Oak"}))
        model = Model()
        checked = await remap(retrieval("Inspect River Birch", mixed), model, config=CONFIG, preserve_content=True)
        self.assertEqual(checked.selected[0].candidate, mixed)
        self.assertEqual(checked.selected[0].candidate.chunk.metrics.event_count, 2)
        self.assertIn("current:literal_matches_are_record_subset_whole_section_metrics_not_entity_totals", checked.gaps)
        payload = model.calls[0][1]["cells"][0]["candidates"][0]
        self.assertEqual(payload["literal_matching_record_indexes"], [0])
        self.assertEqual(payload["literal_matching_record_count"], 1)
        self.assertEqual(payload["observed_metrics"]["events"], 2)

    async def test_numeric_and_boolean_fields_remain_context_without_identity_filters(self):
        first = candidate("first", record("event-a", {"flag": True, "attempt": 1}))
        second = candidate("second", record("event-b", {"flag": False, "attempt": 2}))
        model = Model()
        checked = await remap(retrieval("Is it true after 1 attempt?", first, second), model,
                              config=CONFIG, preserve_content=True)
        self.assertEqual(len(model.calls[0][1]["cells"][0]["candidates"]), 2)
        self.assertEqual(json.loads(checked.provenance[0].detail)["literal_anchor_fields"], 0)

    async def test_nonstandard_json_constants_remain_prose_without_partial_anchors(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                row = record("event-a", {})
                row["message"] = '{"account":"River Birch","value":' + constant + '}'
                first = candidate("first", row)
                second = candidate("second", record("event-b", {"account": "Cedar Oak"}))
                model = Model()
                checked = await remap(retrieval("Inspect River Birch", first, second), model,
                                      config=CONFIG, preserve_content=True)
                self.assertEqual(len(model.calls[0][1]["cells"][0]["candidates"]), 2)
                self.assertEqual(json.loads(checked.provenance[0].detail)["literal_anchor_fields"], 0)

    async def test_anchor_eligibility_does_not_replace_model_relevance_or_membership(self):
        wanted = candidate("wanted", record("event-a", {"account": "River Birch"}))
        for reply in ({"selections": []}, {"selections": [{"evidence_ref": "invented", "reason": "topic_match", "confidence": .9}]}):
            model = Model(reply)
            checked = await remap(retrieval("Inspect River Birch", wanted), model, config=CONFIG, preserve_content=True)
            self.assertEqual(len(model.calls), 1)
            self.assertFalse(checked.selected)


if __name__ == "__main__":
    unittest.main()
