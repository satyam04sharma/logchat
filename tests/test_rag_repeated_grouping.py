"""Transient exact-request reuse; all generation outputs are fixture models."""
from dataclasses import replace
import unittest

from logchat.rag.sections import compact_summary_batch, section_batch, validate_refinement, validate_compact_batch
from tests.test_rag_sections import LocalPartitionModel, batch, event


class RepeatedGroupingTests(unittest.IsolatedAsyncioTestCase):
    async def test_400_unique_events_reuse_requests_with_exact_current_attribution(self):
        original = batch([event(index, "routine operation completed", request_status=200,
                                fingerprint=f"unique-{index}") for index in range(400)])
        model = LocalPartitionModel()
        refined = await section_batch(original, model)
        # One exact message cohort, with every unique record expanded afterward.
        self.assertEqual(model.calls, 1)
        self.assertEqual(len(model.payloads[0]["items"]), 1)
        self.assertEqual(len(refined.event_keys), 400)
        self.assertEqual(sum(chunk.metrics.event_count for chunk in refined.chunks), 400)
        self.assertEqual(sum(chunk.metrics.duration_sum_ms for chunk in refined.chunks), 80000)
        self.assertEqual(refined.coverage, original.coverage)
        validate_refinement(original, refined)
        self.assertEqual(await section_batch(batch(list(reversed([
            event(index, "routine operation completed", request_status=200,
                  fingerprint=f"unique-{index}") for index in range(400)]))), LocalPartitionModel()), refined)

    async def test_cohort_packing_reduces_current_compact_calls_without_prose_reuse(self):
        from tests.test_rag_compact import CompactModel
        original = batch([event(index, "PRIVATE_RAW_CANARY routine operation completed", request_status=200)
                          for index in range(400)])
        sections = await section_batch(original, LocalPartitionModel())
        model = CompactModel("empty_fields")
        compact = await compact_summary_batch(sections, model)
        # Previously one compact call per grouping page (17). Cohorts pack
        # current originals up to the same unchanged 16K section bound.
        self.assertLess(model.calls, 17)
        self.assertEqual(model.calls, len(sections.chunks))
        self.assertEqual(sum(len(payload["records"]) for payload in model.payloads), 400)
        self.assertEqual(sum(chunk.metrics.duration_sum_ms for chunk in compact.chunks), 80000)
        self.assertEqual(compact.event_keys, original.event_keys)
        self.assertNotIn("PRIVATE_RAW_CANARY", str(compact))
        self.assertIs(await compact_summary_batch(compact, None), compact)
        validate_refinement(original, compact)
        validate_compact_batch(compact)

    async def test_cohorts_do_not_generalize_values_outcomes_or_compatibility_cells(self):
        values = [event(0, "lookup failed error_code=E_A"), event(1, "lookup failed error_code=E_B"),
                  event(2, "lookup failed error_code=E_A"),
                  event(3, "lookup failed error_code=E_A", service="other"),
                  event(4, "lookup failed error_code=E_A", level="error"),
                  event(5, "lookup failed error_code=E_A", release="v2")]
        original = batch(values)
        model = LocalPartitionModel("separate")
        refined = await section_batch(original, model)
        self.assertEqual(sorted(chunk.metrics.event_count for chunk in refined.chunks), [1, 1, 1, 1, 2])
        self.assertEqual(model.calls, 2)  # identical singleton requests reuse, cells still expand independently
        validate_refinement(original, refined)

    async def test_distinct_rare_outcome_is_sent_and_exactly_preserved(self):
        original = batch([event(index, "routine operation completed") for index in range(100)] +
                         [event(100, "rare operation failed error_code=E_UNUSUAL_99", request_status=503)])
        model = LocalPartitionModel()
        refined = await section_batch(original, model)
        self.assertTrue(any("E_UNUSUAL_99" in message for payload in model.payloads
                            for item in payload["items"] for message in item["messages"]))
        validate_refinement(original, refined)
        self.assertEqual(sum(chunk.metrics.status_counts.get("503", 0) for chunk in refined.chunks), 1)

    async def test_cache_does_not_cross_calls_scopes_or_model_profiles(self):
        original = batch([event(index, "routine operation completed") for index in range(48)])
        first = LocalPartitionModel()
        await section_batch(original, first)
        self.assertEqual(first.calls, 1)
        await section_batch(original, first)
        self.assertEqual(first.calls, 2)
        from logchat.rag.builder import prepare_preserved_batch
        other = prepare_preserved_batch(replace(original.identity, source_id="other"),
                                        [event(index, "routine operation completed") for index in range(48)],
                                        original.window)
        second = LocalPartitionModel("separate")
        second.chat_model = "other-fixture-model"
        refined = await section_batch(other, second)
        self.assertEqual(second.calls, 1)
        self.assertEqual(sum(chunk.metrics.event_count for chunk in refined.chunks), 48)
        self.assertTrue(set(original.event_keys).isdisjoint(refined.event_keys))
        validate_refinement(other, refined)

    async def test_reused_repair_keeps_complete_review_and_exact_membership(self):
        class Repaired(LocalPartitionModel):
            async def generate(self, instruction, payload, schema):
                self.calls += 1
                self.payloads.append(payload)
                refs = [item["ref"] for item in payload["items"]]
                if "repair_protocol" in payload:
                    return {"assignments": {ref: 0 for ref in refs}}
                return {"sections": [refs[:-1]]}

        original = batch([event(index, f"routine operation {index % 2} completed") for index in range(48)])
        model = Repaired()
        refined = await section_batch(original, model)
        self.assertEqual(model.calls, 3)
        self.assertTrue(all("section_membership_repaired_once_same_model" in chunk.loss_notes
                            for chunk in refined.chunks))
        validate_refinement(original, refined)


if __name__ == "__main__":
    unittest.main()
