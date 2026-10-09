"""Compact storage contracts; model responses here are explicit mock fixtures."""
from dataclasses import asdict, replace
import json
import unittest

from logchat.rag.builder import embed_batch
from logchat.rag.sections import (COMPACT_VERSION, SectionModelError, SummaryConfig,
    compact_records_for_chunk, compact_summary_batch, records_for_chunk, section_batch,
    validate_batch, validate_compact_batch, validate_compact_chunk, validate_refinement,
    _compact_id, _hash, _json)
from tests.test_rag_sections import LocalPartitionModel, SPEC, batch, event


class CompactModel:
    local_only = preserve_content = True
    chat_model = "mock-compact-model"

    def __init__(self, mode="valid"):
        self.mode, self.calls, self.payloads = mode, 0, []

    async def generate(self, instruction, payload, schema):
        self.calls += 1
        self.payloads.append(payload)
        fields = [field["field_ref"] for field in payload["field_catalog"]
                  if field["key"] in {"error_code", "/user/email", "quantity", "active", "nothing", "codes", "/path~1key/~0leaf"}]
        result = {"summary": "The account service rejected multiple lookups with a shared application error.",
                  "important_fields": fields, "uncertainties": ["The initiating cause is not established."]}
        if self.mode == "empty_fields": result["important_fields"] = []
        if self.mode == "invented": result["important_fields"][0] = "f999"
        if self.mode == "renamed": result["important_fields"][0] = "error_code"
        if self.mode == "unknown_ref": result["important_fields"][0] = "e999"
        if self.mode == "duplicate": result["important_fields"].append(result["important_fields"][0])
        if self.mode == "duplicate_unknown": result["important_fields"].extend([result["important_fields"][0], "f999"])
        if self.mode == "raw": result["important_fields"] = [{"key": "message", "value": payload["records"][0]["message"], "event_refs": ["e0"]}]
        if self.mode == "fingerprint": result["important_fields"] = [{"key": "fingerprint", "value": "RAW_FINGERPRINT_CANARY", "event_refs": ["e0"]}]
        if self.mode == "type_changed": result["important_fields"] = [{"key": "quantity", "value": 2.0, "event_refs": ["e0"]}]
        if self.mode == "empty_summary": result["summary"] = ""
        if self.mode == "long_summary": result["summary"] = "x" * 1601
        if self.mode == "echo": result["summary"] = payload["records"][0]["message"]
        if self.mode == "timeout": raise TimeoutError("PRIVATE_DIAGNOSTIC")
        if self.mode == "provider": raise RuntimeError("PRIVATE_DIAGNOSTIC")
        return result


async def sections_fixture():
    events = []
    for index in range(3):
        fields = {"error_code": "E_ACCOUNT_X", "user": {"email": f"visitor{index}@example.com"},
                  "quantity": 2, "active": False, "nothing": None, "codes": ["A", "B"], "path/key": {"~leaf": 19}}
        message = "RAW_BODY_CANARY account lookup rejected.\nStructured fields: " + json.dumps(fields)
        events.append(event(index, message, fingerprint="RAW_FINGERPRINT_CANARY"))
    return await section_batch(batch(events), LocalPartitionModel())


class CompactTests(unittest.IsolatedAsyncioTestCase):
    async def test_abstractive_summary_keeps_exact_selected_fields_but_no_raw_support(self):
        original = await sections_fixture()
        model = CompactModel()
        compact = await compact_summary_batch(original, model)
        chunk = compact.chunks[0]
        self.assertEqual(model.calls, 1)
        self.assertEqual(model.payloads[0]["observed_window"]["span_ms"], 2000)
        self.assertEqual(model.payloads[0]["exact_metrics"]["duration_sum_ms"], 4.5)
        self.assertIn("NOT elapsed", model.payloads[0]["metric_semantics"]["duration_sum_ms"])
        self.assertEqual(chunk.compression_version, COMPACT_VERSION)
        self.assertEqual(chunk.summary_model, model.chat_model)
        self.assertIsNone(chunk.supporting_records)
        self.assertNotIn(chunk.summary, original.chunks[0].summary)
        stored = json.dumps(asdict(compact), default=str)
        self.assertNotIn("RAW_BODY_CANARY", stored)
        self.assertNotIn("RAW_FINGERPRINT_CANARY", stored)
        self.assertNotIn("LOGCHAT_LOCAL_RECORDS", stored)
        evidence = validate_compact_chunk(chunk)
        self.assertTrue(all("message" not in row and "fingerprint" not in row for row in evidence["records"]))
        self.assertEqual(next(field for field in evidence["important_fields"] if field["key"] == "error_code")["event_refs"], ["e0", "e1", "e2"])
        self.assertEqual(next(field for field in evidence["important_fields"] if field["key"] == "active")["value"], False)
        self.assertIsNone(next(field for field in evidence["important_fields"] if field["key"] == "nothing")["value"])
        self.assertEqual(next(field for field in evidence["important_fields"] if field["key"] == "codes")["value"], ["A", "B"])
        self.assertEqual(next(field for field in evidence["important_fields"] if field["key"] == "/path~1key/~0leaf")["value"], 19)
        rows = compact_records_for_chunk(chunk)
        self.assertEqual(len(rows), 3)
        for index, row in enumerate(rows):
            self.assertIn({"key": "/user/email", "value": f"visitor{index}@example.com"}, row["important_fields"])
        self.assertEqual(records_for_chunk(chunk), rows)
        self.assertIn("source_detail_coverage_partial", chunk.loss_notes)
        self.assertIn("model_summary_not_verified_truth", chunk.loss_notes)
        self.assertEqual(compact.coverage, original.coverage)
        validate_refinement(original, compact)
        validate_compact_batch(compact)

    async def test_invented_fields_unknown_refs_raw_fields_and_type_changes_fail(self):
        original = await sections_fixture()
        for mode in ("invented", "renamed", "unknown_ref", "duplicate_unknown", "raw", "fingerprint", "type_changed", "empty_summary", "long_summary", "echo"):
            model = CompactModel(mode)
            with self.subTest(mode=mode), self.assertRaises(SectionModelError):
                await compact_summary_batch(original, model)
            self.assertEqual(model.calls, 1)
        self.assertIsNone(original.chunks[0].compact_evidence)

    async def test_repeated_valid_selection_keeps_exact_fields_membership_and_metrics(self):
        original = await sections_fixture()
        expected = await compact_summary_batch(original, CompactModel())
        actual = await compact_summary_batch(original, CompactModel("duplicate"))
        self.assertEqual(actual.chunks[0].compact_evidence, expected.chunks[0].compact_evidence)
        self.assertEqual(actual.chunks[0].summary, expected.chunks[0].summary)
        self.assertEqual(actual.coverage, expected.coverage)
        self.assertEqual(actual.event_keys, expected.event_keys)
        self.assertIn("compact_exact_duplicate_selections_removed", actual.chunks[0].loss_notes)
        validate_refinement(original, actual)
        validate_compact_batch(actual)

    async def test_empty_selection_preserves_correlation_and_declares_other_loss(self):
        compact = await compact_summary_batch(await sections_fixture(), CompactModel("empty_fields"))
        evidence = validate_compact_chunk(compact.chunks[0])
        self.assertEqual({field["key"] for field in evidence["important_fields"]}, {"error_code", "/user/email"})
        self.assertEqual({field["value"] for field in evidence["important_fields"] if field["key"] == "/user/email"},
                         {"visitor0@example.com", "visitor1@example.com", "visitor2@example.com"})
        self.assertIn("correlation_fields_preserved_independently_of_model_selection", compact.chunks[0].loss_notes)
        self.assertTrue(any("omitted source details" in text for text in evidence["uncertainties"]))
        self.assertIs(await compact_summary_batch(compact, None), compact)
        self.assertIs(await section_batch(compact, None), compact)
        validate_refinement(compact, compact)

    async def test_two_user_values_survive_model_omission_without_retaining_bodies(self):
        events = [event(index, "PRIVATE_RAW_CANARY renewal rejected " + json.dumps({
            "event_id": f"app-{index}", "user": {"email": f"person{index}@example.test", "phoneNumber": f"+1555000000{index}"},
            "traceId": f"trace-{index}", "library_code": "GlyphIndexMismatch", "routine": "not selected"}))
            for index in range(2)]
        original = await section_batch(batch(events), LocalPartitionModel())
        compact = await compact_summary_batch(original, CompactModel("empty_fields"))
        rows = compact_records_for_chunk(compact.chunks[0])
        for index, row in enumerate(rows):
            fields = {item["key"]: item["value"] for item in row["important_fields"]}
            self.assertEqual(fields["/user/email"], f"person{index}@example.test")
            self.assertEqual(fields["/user/phoneNumber"], f"+1555000000{index}")
            self.assertEqual(fields["traceId"], f"trace-{index}")
            self.assertEqual(fields["event_id"], f"app-{index}")
            self.assertEqual(fields["library_code"], "GlyphIndexMismatch")
            self.assertNotIn("routine", fields)
        self.assertNotIn("PRIVATE_RAW_CANARY", json.dumps(asdict(compact), default=str))
        self.assertEqual(compact.chunks[0].metrics, original.chunks[0].metrics)
        validate_refinement(original, compact)

    async def test_too_many_required_correlations_reject_before_model_without_silent_loss(self):
        events = [event(index, "request failed " + json.dumps({f"resource{index}_{n}_id": f"value-{index}-{n}"
                  for n in range(30)})) for index in range(3)]
        original = await section_batch(batch(events), LocalPartitionModel())
        model = CompactModel("empty_fields")
        with self.assertRaises(SectionModelError) as raised:
            await compact_summary_batch(original, model)
        self.assertEqual(raised.exception.category, "compact_input_budget_exceeded")
        self.assertEqual(model.calls, 0)
        self.assertIsNone(original.chunks[0].compact_evidence)

    async def test_source_catalog_cannot_truncate_a_correlation_key_silently(self):
        values = {f"attribute_{number:03}": number for number in range(70)}
        values["zz_email"] = "person@example.test"
        original = await section_batch(batch([event(message="operation refused " + json.dumps(values))]), LocalPartitionModel())
        model = CompactModel("empty_fields")
        with self.assertRaises(SectionModelError) as raised:
            await compact_summary_batch(original, model)
        self.assertEqual(raised.exception.category, "compact_input_budget_exceeded")
        self.assertEqual(model.calls, 0)

    async def test_compact_integrity_scope_metrics_and_raw_support_are_checked(self):
        compact = await compact_summary_batch(await sections_fixture(), CompactModel())
        chunk = compact.chunks[0]
        for altered in (replace(chunk, summary="Changed abstract summary"), replace(chunk, summary_model="another-model"),
                replace(chunk, identity=replace(chunk.identity, environment_id="another-scope")),
                replace(chunk, supporting_records="forbidden raw archive"),
                replace(chunk, metrics=replace(chunk.metrics, duration_sum_ms=999)),
                replace(chunk, compact_evidence=chunk.compact_evidence.replace("E_ACCOUNT_X", "E_INVENTED"))):
            with self.subTest(summary=altered.summary), self.assertRaises(ValueError): validate_compact_chunk(altered)
        with self.assertRaises(ValueError): validate_batch(replace(compact, event_keys=compact.event_keys[:-1]))

    async def test_transient_refinement_checks_selected_values_even_when_hash_is_recomputed(self):
        original = await sections_fixture()
        compact = await compact_summary_batch(original, CompactModel())
        chunk = compact.chunks[0]
        evidence = json.loads(chunk.compact_evidence)
        field = next(item for item in evidence["important_fields"] if item["key"] == "quantity")
        field["value"] = 2.0  # Distinguish original JSON integer from normalized float.
        encoded = _json(evidence)
        changed = replace(chunk, compact_evidence=encoded,
                          pattern_id=_hash([COMPACT_VERSION, chunk.summary, evidence["important_fields"]]))
        changed = replace(changed, chunk_id=_compact_id(changed, encoded, changed.summary, changed.summary_model))
        validate_compact_chunk(changed)  # Originals are intentionally unavailable at this layer.
        with self.assertRaisesRegex(ValueError, "field_mismatch"):
            validate_refinement(original, replace(compact, chunks=(changed,)))

    async def test_unstructured_messages_need_no_catalog_and_catalog_has_no_metadata_alias_noise(self):
        original = await section_batch(batch([event(message="A rare operation failed without structured attributes.")]), LocalPartitionModel())
        model = CompactModel("empty_fields")
        compact = await compact_summary_batch(original, model)
        self.assertEqual(model.payloads[0]["field_catalog"], [])
        validate_compact_batch(compact)
        model = CompactModel()
        await compact_summary_batch(await sections_fixture(), model)
        keys = {field["key"] for field in model.payloads[0]["field_catalog"]}
        self.assertIn("error_code", keys)
        self.assertNotIn("/error_code", keys)
        self.assertNotIn("/metadata/timestamp", keys)
        self.assertIn("/user/email", keys)
        self.assertNotIn("email", keys)

    async def test_embedding_uses_prose_fields_and_metrics_without_raw_rows(self):
        class Provider:
            local_only = preserve_content = True
            spec = SPEC
            async def embed(self, texts, **kwargs):
                self.texts = texts
                return [[1., 2., 3.] for _ in texts]
        compact = await compact_summary_batch(await sections_fixture(), CompactModel())
        provider = Provider()
        result = await embed_batch(compact, provider)
        value = json.loads(provider.texts[0])
        self.assertEqual(set(value), {"model_summary", "important_fields", "exact_metrics"})
        self.assertNotIn("RAW_BODY_CANARY", provider.texts[0])
        self.assertNotIn("RAW_FINGERPRINT_CANARY", provider.texts[0])
        self.assertEqual(value["exact_metrics"]["event_count"], 3)
        self.assertEqual(value["exact_metrics"]["duration_sum_ms"], 4.5)
        self.assertIn("not_verified_truth", next(stage for stage in result.provenance if stage.stage == "summary").detail)

    async def test_model_attestation_budgets_and_errors_are_safe(self):
        original = await sections_fixture()
        for attribute in ("local_only", "preserve_content"):
            model = CompactModel()
            setattr(model, attribute, False)
            with self.assertRaises(SectionModelError): await compact_summary_batch(original, model)
            self.assertEqual(model.calls, 0)
        model = CompactModel()
        with self.assertRaises(SectionModelError) as raised:
            await compact_summary_batch(original, model, config=SummaryConfig(input_character_budget=3000))
        self.assertEqual(raised.exception.category, "compact_input_budget_exceeded")
        self.assertEqual(model.calls, 0)
        for mode, category in (("timeout", "compact_timeout"), ("provider", "compact_provider_unavailable")):
            with self.subTest(mode=mode), self.assertRaises(SectionModelError) as raised:
                await compact_summary_batch(original, CompactModel(mode))
            self.assertEqual(raised.exception.category, category)
            self.assertNotIn("PRIVATE_DIAGNOSTIC", str(raised.exception))


if __name__ == "__main__": unittest.main()
