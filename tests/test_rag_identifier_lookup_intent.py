"""Instruction delivery and strict selection guards, not model quality proofs."""
from dataclasses import replace
import json
import unittest

import httpx

from test_native_model_support import guarded_transport
from test_rag_exact_remapping import CELL, CONFIG, SPEC, candidate, record
from logchat.rag.answering import assemble_context
from logchat.rag.builder import event_key
from logchat.rag.contracts import RetrievalResult, StageProvenance
from logchat.rag.record_view import restore_records
from logchat.rag.remapper import CLASSIFICATION_INSTRUCTION, _validate_output, remap
from logchat.rag.sections import encode_records, records_for_chunk
from pipeline.models import LocalModels


IDENTIFIER = "TRACE_LOOKUP_82"
MESSAGES = ("Queue lease retry deferred", "Directory credentials renewed", "Page render completed")
SERVICES = ("worker", "auth", "web")


def observation(service, identifier, message):
    row = record(identifier, {"incident_id": IDENTIFIER}, message=message)
    item = candidate(identifier, row)
    identity = replace(item.chunk.identity, source_id=service)
    row.update(source=service, service=service, event_key=event_key(identity, identifier))
    chunk = replace(item.chunk, identity=identity, service=service, summary=encode_records((row,)))
    return replace(item, chunk=chunk)


def observations():
    return tuple(observation(service, "event-" + service, message)
                 for service, message in zip(SERVICES, MESSAGES))


def query(question, items):
    # Synthetic upstream provenance permits citation assembly without embedding
    # or retrieval calls; this fixture does not establish actual stage quality.
    return RetrievalResult(question, (replace(CELL, source_ids=SERVICES),), items, (), SPEC,
                           provenance=(StageProvenance("query_embedding", "ok", "mock-only"),
                                       StageProvenance("retrieval", "ok", "mock-only")))


def transport(labels, requests):
    def respond(request):
        requests.append(json.loads(request.content))
        payload = json.loads(requests[-1]["prompt"])
        index = int(payload["candidate"]["candidate_ref"][1:])
        return httpx.Response(200, json={"response": json.dumps({
            "label": labels[index], "confidence": .9}), "done_reason": "stop"})
    return LocalModels(base_url="http://127.0.0.1:11434",
                       transport=guarded_transport(respond), preserve_content=True)


class IdentifierLookupIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_identifier_supplies_divergent_observations_and_selects_original_citations(self):
        items, requests = observations(), []
        checked = await remap(query(IDENTIFIER, items),
                              transport(("exact_identifier", "topic_match", "comparison_baseline"), requests),
                              config=CONFIG, preserve_content=True)
        self.assertEqual(len(requests), 3)
        body = requests[0]
        self.assertTrue(body["system"].startswith(CLASSIFICATION_INSTRUCTION))
        rule = " ".join(body["system"].split())
        self.assertIn("Bare exact identifiers need occurrence in retained records", rule)
        self.assertIn("across scoped sources and services", rule)
        self.assertIn("Other candidates and selection capacity are irrelevant", rule)
        self.assertIn("occurrence in retained records", rule)
        self.assertIn("without extra topic constraints", rule)
        supplied = [json.loads(request["prompt"])["candidate"] for request in requests]
        self.assertEqual([item["candidate_ref"] for item in supplied], ["c0", "c1", "c2"])
        self.assertEqual(body["format"]["required"], ["label", "confidence"])
        self.assertEqual(body["options"], {"temperature": 0, "num_ctx": 8192, "num_predict": 1200})
        self.assertTrue(all("limits" not in json.loads(request["prompt"]) for request in requests))
        for original, supplied_item in zip(items, supplied):
            self.assertEqual(supplied_item["literal_matching_record_indexes"], [0])
            self.assertEqual(restore_records(supplied_item["records"]), [
                {key: value for key, value in row.items() if key != "event_key"}
                for row in records_for_chunk(original.chunk)])
        self.assertEqual(tuple(selected.candidate for selected in checked.selected), items)
        for selected, original in zip(checked.selected, items):
            self.assertIs(selected.candidate, original)
        context = assemble_context(checked, preserve_content=True)
        self.assertEqual(set(context["cited_evidence_ids"]), {item.chunk.chunk_id for item in items})
        self.assertEqual(len(context["citations"]), 3)

    async def test_mixed_topic_can_reject_matching_off_topic_observations(self):
        items, requests = observations(), []
        checked = await remap(query(f"Why was the queue lease retry deferred for {IDENTIFIER}?", items),
                              transport(("topic_match", "not_relevant", "not_relevant"), requests),
                              config=CONFIG, preserve_content=True)
        self.assertEqual(len(requests), 3)
        self.assertIn("identifier matches alone do not suffice", " ".join(requests[0]["system"].split()))
        self.assertIn("Reject unrelated neighbors", requests[0]["system"])
        self.assertEqual([selected.candidate for selected in checked.selected], [items[0]])

    async def test_absent_bare_identifier_supplies_nothing(self):
        requests = []
        checked = await remap(query("TRACE_ABSENT_93", observations()),
                              transport(("exact_identifier",) * 3, requests),
                              config=CONFIG, preserve_content=True)
        self.assertFalse(requests)
        self.assertFalse(checked.selected)
        self.assertIn("no_candidate_record_contains_requested_literal_values", checked.gaps)

    async def test_bare_identifier_cannot_bypass_per_cell_cap(self):
        items = observations()
        extra = observation("web", "event-extra", "Page render retried")
        requests = []
        checked = await remap(query(IDENTIFIER, items + (extra,)),
                              transport(("exact_identifier",) * 4, requests),
                              config=CONFIG, preserve_content=True)
        self.assertEqual(len(requests), 4)
        self.assertEqual(len(checked.selected), 3)
        self.assertEqual(checked.provenance[0].status, "partial")
        detail = json.loads(checked.provenance[0].detail)
        self.assertEqual(detail["positive_labels"], 4)
        self.assertEqual(detail["rejected_labels"], 0)
        self.assertEqual(detail["omitted_by_selection_limit"], 1)
        self.assertIn("current:remapper_cell_selection_limit_reached", checked.gaps)
        # The separate legacy selection contract still rejects four positives.
        admitted = {f"c{index}": item for index, item in enumerate(items + (extra,))}
        with self.assertRaises(ValueError):
            _validate_output({"judgements": {ref: {"label": "exact_identifier", "confidence": .9}
                                           for ref in admitted}}, admitted, CONFIG)
