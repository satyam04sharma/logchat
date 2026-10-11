"""Bounded candidate/selection contracts, not production model quality tests."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from logchat.rag.builder import event_key
from logchat.rag.contracts import (
    Coverage, EmbeddingSpec, RetrievalCell, SearchHit, SemanticChunk, SourceIdentity, TimeWindow,
)
from logchat.rag.remapper import RemapConfig, remap
from logchat.rag.retriever import _rank, _requested_services, RetrievalConfig, plan_cells, retrieve
from logchat.rag.sections import (
    COMPACT_EVIDENCE_VERSION, COMPACT_VERSION, RECORD_PREFIX, SECTION_VERSION, _OMISSION_NOTICE, _compact_id, _hash, _json,
    record_metrics, validate_compact_chunk,
)

START = datetime(2026, 10, 4, tzinfo=timezone.utc)
WINDOW = TimeWindow(START, START + timedelta(hours=1))
SPEC = EmbeddingSpec("mock", "structural-fixture", "no-production-inference", 3)
QUESTION = "What context exists for INC_CORRELATION_73 across web, worker, and auth services?"
CONFIG = RemapConfig(input_character_budget=24000, input_byte_budget=48000)
CELL = plan_cells("owner", "project", ("prod",), WINDOW,
                  source_ids=("web", "worker", "auth"))[0]


def compact(service, index, *, incident="INC_CORRELATION_73", source=None, summary=None,
            code=None, environment="prod", window=WINDOW):
    identity = SourceIdentity("owner", "project", environment, source or service)
    identifier = f"run-{service}-{index:06d}"
    record = {"ref": "e0", "event_key": event_key(identity, identifier), "event_id": identifier,
              "timestamp": window.start.isoformat(), "source": source or service, "service": service,
              "level": "error", "release": None, "duration_ms": 5000, "request_status": 504}
    fields = [{"key": "incident_id", "value": incident, "event_refs": ["e0"]}]
    if code is not None:
        fields.append({"key": "error_code", "value": code, "event_refs": ["e0"]})
    fields.sort(key=lambda field: field["key"])
    evidence = _json({"version": COMPACT_EVIDENCE_VERSION, "records": [record],
                      "important_fields": fields, "uncertainties": [_OMISSION_NOTICE]})
    text = summary or f"{service} request failed during the correlated incident."
    chunk = SemanticChunk("pending", identity, window, service, "error", text,
                          record_metrics((record,)), _hash([COMPACT_VERSION, text, fields]),
                          first_event_at=window.start, last_event_at=window.start, compression_version=COMPACT_VERSION,
                          compact_evidence=evidence, summary_model="mock-summary-fixture")
    chunk = replace(chunk, chunk_id=_compact_id(chunk, evidence, text, chunk.summary_model))
    validate_compact_chunk(chunk)
    return chunk


class Provider:
    spec = SPEC

    async def embed(self, texts, *, purpose):
        return ((1., 0., 0.),)


class Backend:
    embedding_spec = SPEC

    def __init__(self, chunks):
        self.chunks = chunks

    def vector_search(self, cell, vector, *, limit):
        return tuple(SearchHit(chunk, .99, index + 1, "vector") for index, chunk in enumerate(self.scoped(cell)))

    def lexical_search(self, cell, question, *, limit):
        return tuple(SearchHit(chunk, .01, index + 1, "lexical") for index, chunk in enumerate(self.scoped(cell)))

    def scoped(self, cell):
        return [chunk for chunk in self.chunks if chunk.identity.environment_id == cell.environment_id
                and chunk.window.start < cell.window.end and chunk.window.end > cell.window.start
                and (not cell.source_ids or chunk.identity.source_id in cell.source_ids)
                and (cell.service is None or chunk.service == cell.service)]

    def coverage_for(self, cell, *, limit):
        return tuple(Coverage(identity, cell.window, "complete")
                     for identity in dict.fromkeys(chunk.identity for chunk in self.scoped(cell)))


class Selector:
    chat_model = "mock-relevance-fixture"

    def __init__(self, mode="breadth"):
        self.mode, self.calls = mode, []

    async def generate(self, instruction, payload, schema):
        self.calls.append((instruction, payload, schema))
        options = payload["cells"][0]["candidates"]
        seen, chosen = set(), []
        for item in options:
            if self.mode == "partial" and item["service"] == "auth":
                continue  # Explicit mock rejection; never fabricated coverage.
            if item["service"] not in seen:
                chosen.append({"evidence_ref": item["evidence_ref"], "reason": "topic_match",
                               "confidence": .2 if self.mode == "low_auth" and item["service"] == "auth" else .9})
                seen.add(item["service"])
        if self.mode == "empty":
            chosen = []
        return {"selections": chosen[:min(payload["limits"].values())]}


class AlternativeSelector(Selector):
    """Explicit mock: choose one observation per supplied incident alternative."""

    async def generate(self, instruction, payload, schema):
        self.calls.append((instruction, payload, schema))
        seen, selections = set(), []
        for cell in payload["cells"]:
            for candidate in cell["candidates"]:
                values = {field["value"] for row in candidate["records"] for field in row["important_fields"]
                          if field["key"] == "incident_id"}
                if values - seen:
                    selections.append({"evidence_ref": candidate["evidence_ref"], "reason": "comparison_baseline", "confidence": .9})
                    seen.update(values)
        return {"selections": selections}


class SourceDiversityTests(unittest.IsolatedAsyncioTestCase):
    async def test_joint_field_witness_precedes_twelve_partial_matches(self):
        chunks = tuple(compact("web", index) for index in range(12))
        joint = compact("web", 12, code="AUTH_FAIL_73")
        question = "Inspect INC_CORRELATION_73 and AUTH_FAIL_73"
        found = await retrieve(question, (CELL,), Backend(chunks + (joint,)), Provider(), preserve_content=True)
        self.assertIn(joint.chunk_id, {candidate.chunk.chunk_id for candidate in found.candidates})
        self.assertEqual(found.candidates[0].chunk.chunk_id, joint.chunk_id)
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        self.assertEqual([item.candidate.chunk.chunk_id for item in checked.selected], [joint.chunk_id])
        self.assertEqual(len(model.calls), 1)
        ranked, _, capped = _rank(CELL, Backend(chunks + (joint,)).vector_search(CELL, (), limit=48),
                                 Backend(chunks + (joint,)).lexical_search(CELL, question, limit=24),
                                 question, RetrievalConfig(), preserve_content=True)
        self.assertEqual(ranked[0].chunk.chunk_id, joint.chunk_id)
        self.assertTrue(capped)

    async def test_same_field_late_alternative_survives_both_caps(self):
        first = tuple(compact("web", index) for index in range(13))
        second = compact("web", 13, incident="OTHER_INCIDENT_44")
        question = "Compare INC_CORRELATION_73 and OTHER_INCIDENT_44"
        found = await retrieve(question, (CELL,), Backend(first + (second,)), Provider(), preserve_content=True)
        self.assertEqual(len(found.candidates), 12)
        self.assertIn(second.chunk_id, {candidate.chunk.chunk_id for candidate in found.candidates})
        model = AlternativeSelector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        supplied = model.calls[0][1]["cells"][0]["candidates"]
        self.assertEqual(len(supplied), 6)
        self.assertTrue(any(row["important_fields"][0]["value"] == "OTHER_INCIDENT_44"
                            for item in supplied for row in item["records"]))
        self.assertEqual(len(checked.selected), 2)
        self.assertEqual(json.loads(checked.provenance[0].detail)["literal_anchor_fields"], 1)

    async def test_shared_comparison_anchors_span_environment_and_period_cells(self):
        current = tuple(compact("web", index) for index in range(13))
        question = "Compare INC_CORRELATION_73 and OTHER_INCIDENT_44"
        previous_window = TimeWindow(START - timedelta(hours=1), START)
        for scope in ("environment", "period"):
            with self.subTest(scope=scope):
                other = compact("web", 50, incident="OTHER_INCIDENT_44",
                                environment="preview" if scope == "environment" else "prod",
                                window=WINDOW if scope == "environment" else previous_window)
                other_cell = replace(CELL, cell_id="other", environment_id=other.identity.environment_id,
                                     window=other.window)
                found = await retrieve(question, (CELL, other_cell), Backend(current + (other,)), Provider(), preserve_content=True)
                checked = await remap(found, AlternativeSelector(), config=CONFIG, preserve_content=True)
                self.assertEqual({item.candidate.cell_id for item in checked.selected}, {CELL.cell_id, "other"})
                self.assertEqual(len(checked.selected), 2)
                self.assertEqual(json.loads(checked.provenance[0].detail)["literal_anchor_fields"], 1)

    async def test_fields_in_different_records_cannot_manufacture_joint_witness(self):
        first = compact("web", 1)
        second = compact("web", 2, incident="OTHER_INCIDENT_44", code="AUTH_FAIL_73")
        evidence = json.loads(first.compact_evidence)
        extra = json.loads(second.compact_evidence)
        evidence["records"].append({**extra["records"][0], "ref": "e1"})
        evidence["important_fields"] = sorted([
            {"key": "incident_id", "value": "INC_CORRELATION_73", "event_refs": ["e0"]},
            {"key": "error_code", "value": "AUTH_FAIL_73", "event_refs": ["e1"]}], key=lambda item: item["key"])
        encoded = _json(evidence)
        combined = replace(first, compact_evidence=encoded, metrics=record_metrics(evidence["records"]),
                           pattern_id=_hash([COMPACT_VERSION, first.summary, evidence["important_fields"]]))
        combined = replace(combined, chunk_id=_compact_id(combined, encoded, combined.summary, combined.summary_model))
        validate_compact_chunk(combined)
        found = await retrieve("Inspect INC_CORRELATION_73 and AUTH_FAIL_73", (CELL,), Backend((combined,)), Provider(), preserve_content=True)
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        self.assertFalse(checked.selected)
        self.assertFalse(model.calls)

    async def test_witness_requirements_beyond_caps_are_explicit(self):
        chunks = tuple(compact("web", index, incident=f"INC_ALTERNATIVE_{index}") for index in range(13))
        question = "Compare " + " and ".join(f"INC_ALTERNATIVE_{index}" for index in range(13))
        found = await retrieve(question, (CELL,), Backend(chunks), Provider(), preserve_content=True)
        self.assertEqual(len(found.candidates), 12)
        self.assertIn(f"{CELL.cell_id}:literal_or_service_witnesses_exceed_candidate_limit", found.gaps)
        smaller_question = "Compare " + " and ".join(f"INC_ALTERNATIVE_{index}" for index in range(7))
        smaller = await retrieve(smaller_question, (CELL,), Backend(chunks[:7]), Provider(), preserve_content=True)
        model = Selector()
        checked = await remap(smaller, model, config=CONFIG, preserve_content=True)
        self.assertEqual(len(model.calls[0][1]["cells"][0]["candidates"]), 6)
        self.assertIn(f"{CELL.cell_id}:literal_or_service_witnesses_exceed_candidate_limit", checked.gaps)
        self.assertEqual(checked.provenance[0].status, "partial")

    async def test_invalid_retained_cell_stays_failed_without_poisoning_other_cells(self):
        invalid = replace(compact("web", 0), compression_version=SECTION_VERSION,
                          summary=RECORD_PREFIX + "invalid", compact_evidence=None)
        valid = compact("worker", 0)
        cells = (replace(CELL, source_ids=("web",)), replace(CELL, cell_id="worker-cell", source_ids=("worker",)))
        found = await retrieve("Why did requests fail?", cells, Backend((invalid, valid)), Provider(), preserve_content=True)
        self.assertEqual([candidate.chunk.chunk_id for candidate in found.candidates], [valid.chunk_id])
        self.assertIn(f"{CELL.cell_id}:candidate_search_unavailable_or_invalid", found.gaps)

    async def test_twelve_incidents_survive_neighbors_and_existing_caps(self):
        incidents = tuple(compact(service, index) for service in ("web", "worker", "auth") for index in range(4))
        # Unrelated high-scoring vector AND lexical neighbors precede support.
        neighbors = tuple(compact("web", 100 + index, incident="OTHER_INCIDENT_44") for index in range(12))
        found = await retrieve(QUESTION, (CELL,), Backend(neighbors + incidents), Provider(), preserve_content=True)
        self.assertEqual(len(found.candidates), 12)
        self.assertEqual(Counter(c.chunk.service for c in found.candidates), {"web": 4, "worker": 4, "auth": 4})
        self.assertTrue(all("INC_CORRELATION_73" in c.chunk.compact_evidence for c in found.candidates))
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        supplied = model.calls[0][1]["cells"][0]
        self.assertEqual(Counter(item["service"] for item in supplied["candidates"]), {"web": 2, "worker": 2, "auth": 2})
        self.assertEqual(set(supplied["requested_services"]), {"web", "worker", "auth"})
        self.assertEqual({item.candidate.chunk.service for item in checked.selected}, {"web", "worker", "auth"})
        self.assertEqual(len(checked.selected), 3)
        detail = json.loads(checked.provenance[0].detail)
        self.assertLessEqual(detail["input_characters"], CONFIG.input_character_budget)
        self.assertLessEqual(detail["input_utf8_bytes"], CONFIG.input_byte_budget)
        self.assertEqual(detail["requested_service_coverage"][CELL.cell_id]["missing"], [])
        self.assertIn("prioritize one relevant observation per service", model.calls[0][0])
        self.assertEqual(checked, await remap(found, Selector(), config=CONFIG, preserve_content=True))

    async def test_model_rejection_and_low_confidence_are_honest_partial_coverage(self):
        chunks = tuple(compact(service, index) for service in ("web", "worker", "auth") for index in range(4))
        found = await retrieve(QUESTION, (CELL,), Backend(chunks), Provider(), preserve_content=True)
        for mode in ("partial", "low_auth", "empty"):
            with self.subTest(mode=mode):
                checked = await remap(found, Selector(mode), config=CONFIG, preserve_content=True)
                self.assertNotIn("auth", {item.candidate.chunk.service for item in checked.selected})
                self.assertIn(f"{CELL.cell_id}:requested_service_coverage_incomplete", checked.gaps)
                self.assertEqual(checked.provenance[0].status, "empty" if mode == "empty" else "partial")
        unavailable = await remap(found, None, config=CONFIG, preserve_content=True)
        self.assertFalse(unavailable.selected)
        self.assertEqual(unavailable.provenance[0].status, "unavailable")

    async def test_literal_ineligible_service_is_not_admitted_to_fill_breadth(self):
        chunks = tuple(compact(service, index, incident="OTHER_INCIDENT_44" if service == "auth" else "INC_CORRELATION_73")
                       for service in ("web", "worker", "auth") for index in range(4))
        found = await retrieve(QUESTION, (CELL,), Backend(chunks), Provider(), preserve_content=True)
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        self.assertNotIn("auth", {item["service"] for item in model.calls[0][1]["cells"][0]["candidates"]})
        self.assertEqual(checked.provenance[0].status, "partial")
        self.assertIn(f"{CELL.cell_id}:requested_services_without_eligible_evidence", checked.gaps)
        self.assertIn("auth", json.loads(checked.provenance[0].detail)["requested_service_coverage"][CELL.cell_id]["missing"])

    async def test_breadth_cannot_reopen_an_absent_identifier(self):
        chunks = tuple(compact(service, index) for service in ("web", "worker", "auth") for index in range(4))
        question = QUESTION.replace("INC_CORRELATION_73", "UNKNOWN_INCIDENT_97213")
        found = await retrieve(question, (CELL,), Backend(chunks), Provider(), preserve_content=True)
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        self.assertFalse(model.calls)
        self.assertFalse(checked.selected)
        self.assertIn("no_candidate_record_contains_requested_literal_values", checked.gaps)

    async def test_single_service_and_identifier_keep_exact_scope(self):
        chunks = tuple(compact("web", index) for index in range(4))
        found = await retrieve("INC_CORRELATION_73", (replace(CELL, service="web", source_ids=("web",)),),
                               Backend(chunks), Provider(), preserve_content=True)
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        self.assertTrue(checked.selected)
        self.assertTrue(all(item.candidate.chunk.service == "web" for item in checked.selected))
        self.assertNotIn("requested_services", model.calls[0][1]["cells"][0])
        self.assertNotIn("requested_service_coverage", json.loads(checked.provenance[0].detail))

    async def test_many_web_sources_cannot_consume_other_service_slots(self):
        chunks = tuple(compact("web", index, source=f"web-source-{index}") for index in range(10))
        chunks += tuple(compact(service, index) for service in ("worker", "auth") for index in range(4))
        cell = replace(CELL, source_ids=tuple(dict.fromkeys(chunk.identity.source_id for chunk in chunks)))
        found = await retrieve(QUESTION, (cell,), Backend(chunks), Provider(), preserve_content=True)
        self.assertEqual(Counter(c.chunk.service for c in found.candidates), {"web": 4, "worker": 4, "auth": 4})
        model = Selector()
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        supplied = model.calls[0][1]["cells"][0]["candidates"]
        self.assertEqual(Counter(item["service"] for item in supplied), {"web": 2, "worker": 2, "auth": 2})
        self.assertEqual(len({item["source_id"] for item in supplied if item["service"] == "web"}), 2)
        self.assertEqual({item.candidate.chunk.service for item in checked.selected}, {"web", "worker", "auth"})

    async def test_insufficient_selection_or_input_budget_has_explicit_gap(self):
        chunks = tuple(compact(service, 0) for service in ("web", "worker", "auth"))
        found = await retrieve(QUESTION, (CELL,), Backend(chunks), Provider(), preserve_content=True)
        limited = await remap(found, Selector(), config=replace(CONFIG, selected_per_cell=2), preserve_content=True)
        self.assertEqual(len(limited.selected), 2)
        self.assertEqual(limited.provenance[0].status, "partial")
        self.assertIn(f"{CELL.cell_id}:requested_service_coverage_incomplete", limited.gaps)
        model = Selector()
        exhausted = await remap(found, model, config=replace(CONFIG, input_character_budget=3000), preserve_content=True)
        self.assertFalse(model.calls)
        self.assertFalse(exhausted.selected)
        self.assertIn(f"{CELL.cell_id}:remapper_input_budget_reached", exhausted.gaps)
        self.assertIn(f"{CELL.cell_id}:requested_service_coverage_incomplete", exhausted.gaps)

    def test_known_service_mentions_are_atomic_case_insensitive_and_explicit(self):
        from logchat.rag.contracts import Candidate
        candidates = tuple(Candidate(CELL.cell_id, compact(service, 0), .02)
                           for service in ("web", "worker", "auth"))
        self.assertEqual(set(_requested_services("Across WEB, Worker and AUTH", candidates)), {"web", "worker", "auth"})
        self.assertEqual(_requested_services("Investigate web authentication", candidates), ())
        self.assertEqual(_requested_services("webhook workers authorization", candidates), ())


if __name__ == "__main__":
    unittest.main()
