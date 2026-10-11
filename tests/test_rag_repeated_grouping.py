"""Transient exact-request reuse; all generation outputs are fixture models."""
from dataclasses import replace
import unittest

from logchat.rag.sections import section_batch, validate_refinement
from tests.test_rag_sections import LocalPartitionModel, batch, event


class RepeatedGroupingTests(unittest.IsolatedAsyncioTestCase):
    async def test_400_unique_events_reuse_requests_with_exact_current_attribution(self):
        original = batch([event(index, "routine operation completed", request_status=200,
                                fingerprint=f"unique-{index}") for index in range(400)])
        model = LocalPartitionModel()
        refined = await section_batch(original, model)
        # Sixteen full 24-item pages and one 16-item page, each grouped/reviewed
        # once: 34 model calls become four, with no reduction in event count.
        self.assertEqual(model.calls, 4)
        self.assertEqual(len(refined.event_keys), 400)
        self.assertEqual(sum(chunk.metrics.event_count for chunk in refined.chunks), 400)
        self.assertEqual(sum(chunk.metrics.duration_sum_ms for chunk in refined.chunks), 80000)
        self.assertEqual(refined.coverage, original.coverage)
        validate_refinement(original, refined)
        self.assertEqual(await section_batch(batch(list(reversed([
            event(index, "routine operation completed", request_status=200,
                  fingerprint=f"unique-{index}") for index in range(400)]))), LocalPartitionModel()), refined)

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
        self.assertEqual(first.calls, 2)
        await section_batch(original, first)
        self.assertEqual(first.calls, 4)
        from logchat.rag.builder import prepare_preserved_batch
        other = prepare_preserved_batch(replace(original.identity, source_id="other"),
                                        [event(index, "routine operation completed") for index in range(48)],
                                        original.window)
        second = LocalPartitionModel("separate")
        second.chat_model = "other-fixture-model"
        refined = await section_batch(other, second)
        self.assertEqual(second.calls, 1)
        self.assertEqual(len(refined.chunks), 48)
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

        original = batch([event(index, "routine operation completed") for index in range(48)])
        model = Repaired()
        refined = await section_batch(original, model)
        self.assertEqual(model.calls, 3)
        self.assertTrue(all("section_membership_repaired_once_same_model" in chunk.loss_notes
                            for chunk in refined.chunks))
        validate_refinement(original, refined)


if __name__ == "__main__":
    unittest.main()
