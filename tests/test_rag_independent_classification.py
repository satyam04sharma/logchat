"""Structural controls only; no actual model relevance or acceptance claims."""
from dataclasses import replace
import asyncio
import hashlib
import json
import unittest

import httpx
from test_native_model_support import guarded_transport
from pipeline.models import LocalModels, ModelUnavailable

from test_rag_remapper import CELL, candidate, retrieval
from logchat.rag.remapper import (
    CLASSIFICATION_CALL_LIMIT, CLASSIFICATION_INSTRUCTION, CLASSIFICATION_PROTOCOL,
    RemapConfig, _candidate_payload, _classification_payload, _classification_plan,
    _classification_schema, _validate_classification, _validate_output,
    _execute_classification_plan, remap, CLASSIFICATION_IMPLEMENTATION,
)
from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json


class IndependentClassificationTests(unittest.TestCase):
    def test_flat_protocol_accepts_independent_positives_without_selection_quota(self):
        schema = _classification_schema()
        self.assertEqual(schema["required"], ["label", "confidence"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"label", "confidence"})
        self.assertNotIn("selection_limit", schema["properties"]["label"]["enum"])
        for label in schema["properties"]["label"]["enum"]:
            self.assertEqual(_validate_classification({"label": label, "confidence": 1}), (label, 1.0))
        # The old contract still counts all four positives before confidence.
        admitted = {f"c{i}": candidate(str(i)) for i in range(4)}
        for confidence in (.1, .9):
            value = {"label": "topic_match", "confidence": confidence}
            for _ in admitted:
                self.assertEqual(_validate_classification(value), ("topic_match", confidence))
            with self.assertRaisesRegex(ValueError, "selection_count_bound_exceeded|per_cell_selection_bound_exceeded"):
                _validate_output({"judgements": dict.fromkeys(admitted, value)}, admitted, RemapConfig())

    def test_entire_object_and_negative_confidence_are_strict(self):
        invalid = [None, [], {}, {"judgements": {}}, {"label": "topic_match"},
                   {"label": "selection_limit", "confidence": .9},
                   {"label": "unknown", "confidence": .9},
                   {"label": [], "confidence": .9},
                   {"label": "topic_match", "confidence": .9, "candidate_ref": "c0"}]
        for label in ("topic_match", "not_relevant"):
            invalid.extend({"label": label, "confidence": confidence} for confidence in
                           (True, None, "high", float("nan"), float("inf"), -.1, 1.1, 10 ** 1000))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_classification(value)

    def test_single_projection_retains_complete_records_and_global_context(self):
        item = candidate()
        key = (item.cell_id, item.chunk.chunk_id)
        rows = ({"message": "雪 AUTH-42", "code": "AUTH-42", "status": 401,
                 "nested": {"flags": [True, None, 0.5]}},
                {"message": "other", "code": "OTHER-42", "status": 200})
        records, matched = {key: rows}, {key: (0,)}
        anchors = {("code",): {"AUTH-42", "OTHER-42"}}
        payload = _classification_payload(retrieval((item,)), CELL, item, "c7",
            preserve_content=True, records=records, matched=matched, anchors=anchors,
            requested_services=("web", "worker"))
        self.assertEqual(payload["protocol"], CLASSIFICATION_PROTOCOL)
        self.assertEqual(payload["question"], retrieval().question)
        self.assertEqual(payload["cell"]["requested_services"], ["web", "worker"])
        self.assertEqual(payload["requested_literal_values"], ["AUTH-42", "OTHER-42"])
        self.assertEqual(model_json(payload["candidate"]), model_json(
            _candidate_payload(item, "c7", records=rows, matched=(0,))))
        self.assertEqual(payload["candidate"]["literal_matching_record_indexes"], [0])
        self.assertEqual(payload["candidate"]["observed_metrics"]["scope"], "whole_section_not_requested_entity")
        self.assertNotIn("limits", payload)
        self.assertNotIn("candidates", payload)
        with self.assertRaisesRegex(ValueError, "classification_cell_binding_violation"):
            _classification_payload(retrieval(), replace(CELL, cell_id="other"), item, "c0")

    def test_round_robin_eight_call_cap_and_distinct_cell_chunk_binding(self):
        cells = tuple(replace(CELL, cell_id=f"cell{i}") for i in range(3))
        options = {cell.cell_id: [candidate(str(i), cell=cell) for i in range(6)] for cell in cells}
        data = retrieval(tuple(item for pool in options.values() for item in pool), cells)
        calls, omissions, chars, size = _classification_plan(data, options,
            RemapConfig(input_character_budget=24000, input_byte_budget=48000))
        self.assertEqual(len(calls), CLASSIFICATION_CALL_LIMIT)
        self.assertEqual([item.cell_id for _, item, _ in calls],
                         [cell.cell_id for cell in cells] * 2 + [cells[0].cell_id, cells[1].cell_id])
        self.assertEqual(len({ref for ref, _, _ in calls}), 8)
        self.assertEqual(len(omissions), 10)
        self.assertEqual(set(omissions.values()), {"classification_call_limit"})
        self.assertGreater(chars, 0)
        self.assertEqual(chars, size)
        # Selection caps do not alter classification semantics or planning.
        low = _classification_plan(data, options, RemapConfig(selected_per_cell=1, selected_total=1,
            input_character_budget=24000, input_byte_budget=48000))
        self.assertEqual(low, (calls, omissions, chars, size))

    def test_more_cells_than_call_capacity_are_visibly_unclassified(self):
        cells = tuple(replace(CELL, cell_id=f"cell{i}") for i in range(9))
        options = {cell.cell_id: [candidate("same", cell=cell)] for cell in cells}
        calls, omitted, _, _ = _classification_plan(retrieval((), cells), options,
            RemapConfig(input_character_budget=24000, input_byte_budget=48000))
        self.assertEqual(len(calls), 8)
        self.assertEqual(omitted, {(cells[-1].cell_id, "same"): "classification_call_limit"})

    def test_aggregate_repeated_overhead_exact_boundaries_and_unicode(self):
        item = candidate(summary="雪" * 300)
        data = retrieval((item,), question="Investigate credentials " + "detail " * 80)
        options = {CELL.cell_id: [item]}
        generous = RemapConfig(input_character_budget=24000, input_byte_budget=48000)
        calls, _, chars, size = _classification_plan(data, options, generous)
        self.assertEqual(len(calls), 1)
        texts = (CLASSIFICATION_INSTRUCTION + SYSTEM_SAFETY_SUFFIX,
                 model_json(_classification_schema()), model_json(calls[0][2]))
        self.assertEqual(chars, sum(map(len, texts)))
        self.assertEqual(size, sum(len(text.encode("utf-8")) for text in texts))
        self.assertGreater(size, chars)
        for config in (replace(generous, input_character_budget=chars),
                       replace(generous, input_byte_budget=size)):
            self.assertEqual(len(_classification_plan(data, options, config)[0]), 1)
        for config in (replace(generous, input_character_budget=chars - 1),
                       replace(generous, input_byte_budget=size - 1)):
            planned, omitted, measured, measured_bytes = _classification_plan(data, options, config)
            self.assertFalse(planned)
            self.assertEqual(set(omitted.values()), {"input_budget"})
            self.assertEqual((measured, measured_bytes), (0, 0))
        second = candidate("second", summary=item.chunk.summary)
        planned, omitted, measured, _ = _classification_plan(data,
            {CELL.cell_id: [item, second]}, replace(generous, input_character_budget=chars))
        self.assertEqual(len(planned), 1)
        self.assertEqual(omitted, {(CELL.cell_id, "second"): "input_budget"})
        self.assertEqual(measured, chars)

    def test_oversize_candidate_is_atomic_and_admission_never_exceeds_six(self):
        large = candidate("large", summary="雪" * 4000)
        small = candidate("small")
        config = RemapConfig(input_character_budget=24000, input_byte_budget=6000)
        calls, omitted, _, size = _classification_plan(retrieval((large, small)),
            {CELL.cell_id: [large, small]}, config)
        self.assertEqual([item for _, item, _ in calls], [small])
        self.assertEqual(omitted, {(CELL.cell_id, "large"): "input_budget"})
        self.assertLessEqual(size, config.input_byte_budget)
        self.assertEqual(calls[0][2]["candidate"], _candidate_payload(small, calls[0][0]))
        options = {CELL.cell_id: [candidate(str(i)) for i in range(12)]}
        calls, _, _, _ = _classification_plan(retrieval(), options,
            RemapConfig(candidates_per_cell=20, input_character_budget=24000, input_byte_budget=48000))
        self.assertEqual(len(calls), 6)


class SequenceModel:
    """Explicit flat outcomes at the production generate seam; no real calls."""
    def __init__(self, values, delay=0):
        self.values = iter(values)
        self.delay = delay
        self.calls = []
        self.cancelled = False

    async def generate(self, instruction, payload, schema):
        self.calls.append((instruction, payload, schema))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


POSITIVE = {"label": "topic_match", "confidence": .9}
NEGATIVE = {"label": "not_relevant", "confidence": .9}


class ActiveIndependentTests(unittest.IsolatedAsyncioTestCase):
    config = RemapConfig(input_character_budget=24000, input_byte_budget=48000)

    def detail(self, result):
        self.assertEqual(result.provenance[0].implementation, CLASSIFICATION_IMPLEMENTATION)
        return json.loads(result.provenance[0].detail)

    async def test_active_six_positives_select_three_without_legacy_cap_validation(self):
        items = tuple(candidate(str(i)) for i in range(6))
        model = SequenceModel([POSITIVE] * 6)
        result = await remap(retrieval(items), model, config=self.config)
        self.assertEqual([item.candidate for item in result.selected], list(items[:3]))
        detail = self.detail(result)
        self.assertEqual(detail["validated_classifications"], 6)
        self.assertEqual(detail["positive_labels"], 6)
        self.assertEqual(detail["rejected_labels"], 0)
        self.assertEqual(detail["omitted_by_selection_limit"], 3)
        self.assertIn("current:remapper_cell_selection_limit_reached", result.gaps)
        self.assertEqual(result.provenance[0].status, "partial")
        for instruction, payload, schema in model.calls:
            self.assertEqual(instruction, CLASSIFICATION_INSTRUCTION)
            self.assertEqual(schema, _classification_schema())
            self.assertIn("candidate_ref", payload["candidate"])
            self.assertNotIn("cells", payload)

    def service_items(self, cell):
        return tuple(candidate(f"{service}{i}", cell=cell, service=service,
                               identity=replace(candidate().chunk.identity, source_id=f"source{i}"))
                     for service in ("web", "auth", "worker") for i in range(4))

    async def test_twelve_memories_have_fair_exposure_and_positive_service_breadth(self):
        cell = replace(CELL, service=None, source_ids=())
        items = self.service_items(cell)
        model = SequenceModel([POSITIVE] * 6)
        result = await remap(retrieval(items, (cell,), question="Compare web auth worker sign-in behavior"),
                             model, config=self.config)
        exposed = [payload["candidate"]["service"] for _, payload, _ in model.calls]
        self.assertEqual(exposed, ["web", "auth", "worker"] * 2)
        self.assertEqual([item.candidate.chunk.service for item in result.selected], ["web", "auth", "worker"])
        self.assertEqual(len({item.candidate.chunk.identity.source_id for item in result.selected}), 1)
        self.assertEqual([payload["candidate"]["source_id"] for _, payload, _ in model.calls],
                         ["source0"] * 3 + ["source1"] * 3)
        detail = self.detail(result)
        self.assertEqual(detail["admission_bounded_candidates"], 6)
        self.assertEqual(detail["omitted_by_admission_limit"], 6)
        self.assertEqual(detail["omitted_by_selection_limit"], 3)

    async def test_negative_and_low_positive_service_support_cannot_be_filled(self):
        cell = replace(CELL, service=None, source_ids=())
        model = SequenceModel([POSITIVE, NEGATIVE, dict(POSITIVE, confidence=.1)] * 2)
        result = await remap(retrieval(self.service_items(cell), (cell,),
                                      question="Compare web auth worker sign-in behavior"), model, config=self.config)
        self.assertEqual([item.candidate.chunk.service for item in result.selected], ["web", "web"])
        self.assertEqual([item.candidate.chunk.identity.source_id for item in result.selected], ["source0", "source1"])
        detail = self.detail(result)
        self.assertEqual(detail["rejected_labels"], 2)
        self.assertEqual(detail["below_confidence_threshold"], 2)
        self.assertEqual(detail["omitted_by_selection_limit"], 0)
        self.assertEqual(detail["requested_service_coverage"]["current"]["missing"], ["auth", "worker"])
        self.assertIn("current:requested_services_without_positive_support", result.gaps)

    async def test_custom_twenty_admission_explicitly_omits_candidates_after_six(self):
        items = tuple(candidate(str(i)) for i in range(20))
        result = await remap(retrieval(items), SequenceModel([POSITIVE] * 6),
                             config=replace(self.config, candidates_per_cell=20, selected_per_cell=8))
        detail = self.detail(result)
        self.assertEqual(detail["admission_limit"], 6)
        self.assertEqual(detail["omitted_by_admission_limit"], 14)
        self.assertEqual(detail["planned_classification_calls"], 6)
        self.assertEqual(len(result.selected), 3)
        self.assertIn("current:remapper_candidate_limit_reached", result.gaps)

    async def test_stable_round_robin_selection_and_distinct_repeated_chunk_ids(self):
        cells = tuple(replace(CELL, cell_id=f"cell{i}") for i in range(3))
        items = tuple(candidate(str(i), cell=cell) for cell in cells for i in range(4))
        model = SequenceModel([POSITIVE] * 8)
        result = await remap(retrieval(items, cells), model, config=replace(self.config, selected_total=16))
        expected = [(cell.cell_id, str(i)) for i in range(2) for cell in cells]
        expected.extend([(cells[0].cell_id, "2"), (cells[1].cell_id, "2")])
        self.assertEqual([(x.candidate.cell_id, x.candidate.chunk.chunk_id) for x in result.selected], expected)
        detail = self.detail(result)
        self.assertEqual(detail["attempted_classification_calls"], 8)
        self.assertEqual(detail["omitted_by_call_limit"], 4)
        self.assertEqual(detail["selected_total_limit"], 8)
        self.assertEqual(len({row["binding_hash"] for row in detail["classification_calls"]}), 8)

    async def test_active_global_selection_omissions_are_distinct_from_rejections(self):
        cells = tuple(replace(CELL, cell_id=f"cell{i}") for i in range(3))
        items = tuple(candidate(str(i), cell=cell) for cell in cells for i in range(2))
        result = await remap(retrieval(items, cells), SequenceModel([POSITIVE] * 6),
                             config=replace(self.config, selected_total=2))
        self.assertEqual([x.candidate.cell_id for x in result.selected], ["cell0", "cell1"])
        detail = self.detail(result)
        self.assertEqual(detail["positive_labels"], 6)
        self.assertEqual(detail["omitted_by_selection_limit"], 4)
        self.assertEqual(detail["rejected_labels"], 0)
        self.assertIn("cell2:remapper_global_selection_limit_reached", result.gaps)

    async def test_ninth_cell_has_visible_unclassified_gap(self):
        cells = tuple(replace(CELL, cell_id=f"cell{i}") for i in range(9))
        result = await remap(retrieval(tuple(candidate("same", cell=cell) for cell in cells), cells),
                             SequenceModel([POSITIVE] * 8), config=self.config)
        detail = self.detail(result)
        self.assertEqual(detail["omitted_by_call_limit"], 1)
        self.assertIn("cell8:remapper_classification_call_limit_reached", result.gaps)
        self.assertIn("cell8:no_validated_relevant_evidence", result.gaps)
        self.assertEqual(result.provenance[0].status, "partial")

    async def test_active_late_failure_is_atomic_and_ledger_counts_survive(self):
        items = tuple(candidate(str(i)) for i in range(3))
        for bad in ({}, {"label": "not_relevant", "confidence": True}, ModelUnavailable("private")):
            with self.subTest(bad=bad):
                model = SequenceModel([POSITIVE, bad, POSITIVE])
                result = await remap(retrieval(items), model, config=self.config)
                self.assertFalse(result.selected)
                detail = self.detail(result)
                self.assertEqual(detail["attempted_classification_calls"], 2)
                self.assertEqual(detail["validated_classifications"], 1)
                self.assertEqual(detail["positive_labels"], 0)
                self.assertFalse(detail["classification_calls"][2]["attempted"])
                self.assertNotIn("private", json.dumps(detail))

    async def test_active_deadline_cancels_pending_call_without_reset(self):
        items = tuple(candidate(str(i)) for i in range(3))
        model = SequenceModel([POSITIVE] * 3, delay=.04)
        result = await remap(retrieval(items), model, config=replace(self.config, timeout_seconds=.065))
        self.assertFalse(result.selected)
        self.assertTrue(model.cancelled)
        self.assertEqual(len(model.calls), 2)
        detail = self.detail(result)
        self.assertEqual(detail["reason"], "model_timeout")
        self.assertEqual(detail["validated_classifications"], 1)

    async def test_active_planning_time_is_charged_before_first_call(self):
        from unittest.mock import patch
        import time
        original = _classification_plan

        def slow_plan(*args, **kwargs):
            time.sleep(.02)
            return original(*args, **kwargs)

        model = SequenceModel([POSITIVE])
        with patch("logchat.rag.remapper._classification_plan", side_effect=slow_plan):
            result = await remap(retrieval((candidate(),)), model,
                                 config=replace(self.config, timeout_seconds=.01))
        self.assertFalse(result.selected)
        self.assertFalse(model.calls)
        self.assertEqual(self.detail(result)["reason"], "model_timeout")

    async def test_active_literal_alternatives_use_positive_witnesses_and_exact_records(self):
        from test_rag_exact_remapping import candidate as exact_candidate, record, retrieval as exact_query
        from logchat.rag.record_view import restore_records
        first = exact_candidate("first", record("a", {"account": "River Birch"}))
        repeat = exact_candidate("repeat", record("b", {"account": "River Birch"}))
        second = exact_candidate("second", record("c", {"account": "Cedar Oak"}))
        query = exact_query("Compare River Birch with Cedar Oak", first, repeat, second)
        model = SequenceModel([POSITIVE] * 3)
        result = await remap(query, model, config=replace(self.config, selected_per_cell=2), preserve_content=True)
        self.assertEqual([x.candidate for x in result.selected], [first, second])
        for _, payload, _ in model.calls:
            self.assertEqual(payload["requested_literal_values"], ["Cedar Oak", "River Birch"])
            self.assertEqual(payload["candidate"]["literal_matching_record_indexes"], [0])
        supplied = model.calls[0][1]["candidate"]
        self.assertEqual(restore_records(supplied["records"]), [{k: v for k, v in
                         record("a", {"account": "River Birch"}).items() if k != "event_key"}])
        self.assertFalse(any("requested_literal_alternatives_uncovered" in gap for gap in result.gaps))
        # Witness order is first, second, repeat. A negative alternative stays
        # excluded; repeats cannot manufacture support for its literal value.
        result = await remap(query, SequenceModel([POSITIVE, NEGATIVE, POSITIVE]),
                             config=replace(self.config, selected_per_cell=2), preserve_content=True)
        self.assertEqual([x.candidate for x in result.selected], [first, repeat])
        self.assertIn("current:requested_literal_alternatives_uncovered", result.gaps)

    async def test_active_same_record_literal_gate_excludes_cross_record_neighbors(self):
        from test_rag_exact_remapping import candidate as exact_candidate, record, retrieval as exact_query
        cross = exact_candidate("cross", record("a", {"account": "River Birch", "code": "STORE-7"}),
                                record("b", {"account": "Cedar Oak", "code": "AUTH-9"}))
        wanted = exact_candidate("wanted", record("c", {"account": "River Birch", "code": "AUTH-9"}))
        model = SequenceModel([POSITIVE])
        result = await remap(exact_query("Explain River Birch and AUTH-9.", cross, wanted),
                             model, config=self.config, preserve_content=True)
        self.assertEqual([x.candidate for x in result.selected], [wanted])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.detail(result)["literal_nonmatching_candidates"], 1)

    async def test_active_native_late_duplicate_key_failure_publishes_nothing(self):
        values = iter((json.dumps(POSITIVE), '{"label":"topic_match","label":"not_relevant","confidence":0.9}'))
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"response": next(values), "done_reason": "stop"})

        model = LocalModels(transport=guarded_transport(respond))
        result = await remap(retrieval(tuple(candidate(str(i)) for i in range(3))), model, config=self.config)
        self.assertFalse(result.selected)
        self.assertEqual(result.provenance[0].status, "unavailable")
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.detail(result)["validated_classifications"], 1)
        self.assertEqual(requests[0]["format"], _classification_schema())
        self.assertEqual(requests[0]["options"], {"temperature": 0, "num_ctx": 8192, "num_predict": 1200})

    async def test_active_external_cancellation_propagates_and_stops_calls(self):
        model = SequenceModel([POSITIVE] * 3, delay=1)
        task = asyncio.create_task(remap(retrieval(tuple(candidate(str(i)) for i in range(3))),
                                        model, config=self.config))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(model.cancelled)
        self.assertEqual(len(model.calls), 1)


class IndependentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def plan(self, count=3, *, config=None, cells=(CELL,)):
        config = config or RemapConfig(input_character_budget=24000, input_byte_budget=48000)
        options = {cell.cell_id: [candidate(str(i), cell=cell) for i in range(count)] for cell in cells}
        calls, _, _, _ = _classification_plan(retrieval((), cells), options, config)
        return calls, config

    async def test_sequential_flat_outcomes_have_exact_safe_ordered_ledger(self):
        calls, config = self.plan()
        model = SequenceModel([POSITIVE, NEGATIVE, {"label": "exact_identifier", "confidence": .1}])
        checked, detail, failure = await _execute_classification_plan(calls, model, config)
        self.assertIsNone(failure)
        self.assertEqual(list(checked), [(CELL.cell_id, str(i)) for i in range(3)])
        self.assertEqual(checked[(CELL.cell_id, "1")], ("not_relevant", .9))
        self.assertEqual(checked[(CELL.cell_id, "2")], ("exact_identifier", .1))
        for counter in ("planned_classification_calls", "attempted_classification_calls",
                        "completed_classification_calls", "validated_classifications"):
            self.assertEqual(detail[counter], 3)
        for i, (row, (reference, item, payload)) in enumerate(zip(detail["classification_calls"], calls)):
            self.assertEqual(row["call_index"], i + 1)
            self.assertEqual(row["candidate_ref"], reference)
            texts = (CLASSIFICATION_INSTRUCTION + SYSTEM_SAFETY_SUFFIX,
                     model_json(_classification_schema()), model_json(payload))
            self.assertEqual(row["request_hash"], hashlib.sha256(model_json(texts).encode()).hexdigest())
            self.assertEqual(model.calls[i], (CLASSIFICATION_INSTRUCTION, payload, _classification_schema()))
            self.assertTrue(row["attempted"] and row["returned"] and row["validated"])
            self.assertIsNone(row["native_input_tokens"])
            self.assertIsNone(row["provider_input_tokens"])
            self.assertIsNone(row["stop_reason"])
        serialized = json.dumps(detail)
        self.assertNotIn(calls[0][2]["candidate"]["template"], serialized)
        self.assertEqual(detail["output_characters"], sum(len(json.dumps(value)) for value in
            (POSITIVE, NEGATIVE, {"label": "exact_identifier", "confidence": .1})))

    async def test_late_invalid_flat_outputs_discard_all_earlier_judgements(self):
        calls, config = self.plan()
        invalid = [{}, {"judgements": {"c1": POSITIVE}}, dict(POSITIVE, candidate_ref="c1"),
                   {"label": "topic_match"}, {"label": "selection_limit", "confidence": .9}]
        invalid.extend({"label": "not_relevant", "confidence": confidence} for confidence in
                       (True, "high", float("nan"), float("inf"), 10 ** 1000, -.1, 1.1))
        for value in invalid:
            with self.subTest(value=value):
                model = SequenceModel([POSITIVE, value, POSITIVE])
                checked, detail, failure = await _execute_classification_plan(calls, model, config)
                self.assertFalse(checked)
                self.assertEqual(failure, "invalid_classification")
                self.assertEqual(detail["attempted_classification_calls"], 2)
                self.assertEqual(detail["completed_classification_calls"], 2)
                self.assertEqual(detail["validated_classifications"], 1)
                self.assertFalse(detail["classification_calls"][2]["attempted"])
                self.assertEqual(len(model.calls), 2)

    async def test_late_provider_failure_keeps_counts_without_exception_content(self):
        calls, config = self.plan()
        model = SequenceModel([POSITIVE, ModelUnavailable("private provider body secret"), POSITIVE])
        checked, detail, failure = await _execute_classification_plan(calls, model, config)
        self.assertFalse(checked)
        self.assertEqual(failure, "model_unavailable_or_invalid")
        self.assertEqual(detail["attempted_classification_calls"], 2)
        self.assertEqual(detail["completed_classification_calls"], 1)
        self.assertEqual(detail["validated_classifications"], 1)
        self.assertNotIn("secret", json.dumps(detail))

    async def test_late_native_corruption_and_context_guard_failure_are_atomic(self):
        calls, config = self.plan()
        corruptions = ('{', '{"label":"topic_match","label":"not_relevant","confidence":0.9}',
                       '{"label":"not_relevant","confidence":1e999}',
                       '{"label":"not_relevant"}', json.dumps(dict(NEGATIVE, extra=True)))
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                replies = iter((json.dumps(POSITIVE), corruption))
                requests = []

                def respond(request):
                    requests.append(request)
                    return httpx.Response(200, json={"response": next(replies), "done_reason": "stop"})

                checked, detail, failure = await _execute_classification_plan(
                    calls, LocalModels(transport=guarded_transport(respond)), config)
                self.assertFalse(checked)
                # The native number schema currently permits decoded infinity;
                # the strict flat validator must reject it after return.
                returned_overflow = corruption.endswith('1e999}')
                self.assertEqual(failure, "invalid_classification" if returned_overflow
                                 else "model_unavailable_or_invalid")
                self.assertEqual(len(requests), 2)
                self.assertEqual(detail["completed_classification_calls"], 2 if returned_overflow else 1)
                self.assertEqual(detail["validated_classifications"], 1)
        # Same seam simulates a failed native guard before the second generation.
        class GuardFailure(SequenceModel):
            async def generate(self, instruction, payload, schema):
                if self.calls:
                    raise ModelUnavailable("native context capacity exceeded")
                return await super().generate(instruction, payload, schema)

        checked, detail, failure = await _execute_classification_plan(calls, GuardFailure([POSITIVE]), config)
        self.assertFalse(checked)
        self.assertEqual(failure, "model_unavailable_or_invalid")
        self.assertEqual(detail["attempted_classification_calls"], 2)
        self.assertEqual(detail["completed_classification_calls"], 1)

    async def test_aggregate_output_limit_is_atomic_even_for_valid_negatives(self):
        cells = tuple(replace(CELL, cell_id=str(i)) for i in range(3))
        config = RemapConfig(input_character_budget=24000, input_byte_budget=48000,
                            output_character_budget=500)
        calls, config = self.plan(count=3, config=config, cells=cells)
        value = {"label": "comparison_baseline", "confidence": .12345678901234568}
        self.assertGreater(len(json.dumps(value)) * len(calls), 500)
        checked, detail, failure = await _execute_classification_plan(
            calls, SequenceModel([value] * len(calls)), config)
        self.assertFalse(checked)
        self.assertEqual(failure, "invalid_classification")
        self.assertGreater(detail["validated_classifications"], 0)
        self.assertGreater(detail["output_characters"], 500)
        self.assertEqual(detail["completed_classification_calls"], detail["validated_classifications"] + 1)

    async def test_one_deadline_cancels_pending_call_and_stops_later_calls(self):
        calls, config = self.plan()
        model = SequenceModel([POSITIVE] * 3, delay=.06)
        deadline = asyncio.get_running_loop().time() + .1
        checked, detail, failure = await _execute_classification_plan(calls, model, config, deadline=deadline)
        self.assertFalse(checked)
        self.assertEqual(failure, "model_timeout")
        self.assertTrue(model.cancelled)
        self.assertEqual(detail["attempted_classification_calls"], 2)
        self.assertEqual(detail["completed_classification_calls"], 1)
        self.assertEqual(detail["validated_classifications"], 1)
        self.assertFalse(detail["classification_calls"][2]["attempted"])

    async def test_expired_query_deadline_skips_all_calls_and_external_cancel_propagates(self):
        calls, config = self.plan()
        model = SequenceModel([POSITIVE] * 3)
        checked, detail, failure = await _execute_classification_plan(
            calls, model, config, deadline=asyncio.get_running_loop().time() - 1)
        self.assertFalse(checked)
        self.assertEqual(failure, "model_timeout")
        self.assertEqual(detail["attempted_classification_calls"], 0)
        self.assertFalse(model.calls)
        waiting = SequenceModel([POSITIVE] * 3, delay=1)
        task = asyncio.create_task(_execute_classification_plan(calls, waiting, config))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(waiting.cancelled)
        self.assertEqual(len(waiting.calls), 1)

    async def test_corrupted_plan_bindings_and_aggregate_reservation_fail_before_access(self):
        calls, config = self.plan()
        ref, item, payload = calls[0]
        corrupted = [(ref, item, dict(payload, candidate=dict(payload["candidate"], candidate_ref="swapped")))]
        model = SequenceModel([POSITIVE])
        for plan in (corrupted, [calls[0], calls[0]]):
            with self.assertRaisesRegex(ValueError, "invalid_classification_plan_binding"):
                await _execute_classification_plan(plan, model, config)
        with self.assertRaisesRegex(ValueError, "classification_plan_input_budget_exceeded"):
            await _execute_classification_plan(calls, model, replace(config, input_byte_budget=3000))
        self.assertFalse(model.calls)

    async def test_same_chunk_in_distinct_cells_remains_independently_bound(self):
        other = replace(CELL, cell_id="other")
        calls, config = self.plan(count=1, cells=(CELL, other))
        checked, detail, failure = await _execute_classification_plan(
            calls, SequenceModel([POSITIVE, NEGATIVE]), config)
        self.assertIsNone(failure)
        self.assertEqual(set(checked), {(CELL.cell_id, "0"), (other.cell_id, "0")})
        hashes = [row["binding_hash"] for row in detail["classification_calls"]]
        self.assertEqual(len(set(hashes)), 2)
