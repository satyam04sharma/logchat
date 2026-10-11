"""Versioned semantic transport; all data and vectors here are synthetic fixtures."""
from dataclasses import asdict, replace
import json
import unittest

from logchat.rag.builder import embed_batch
from logchat.rag.scheduler import _decode, _encode
from logchat.rag.sections import (
    COMPACT_VERSION, COMPACT_EVIDENCE_V1, COMPACT_EVIDENCE_V2, COMPACT_EVIDENCE_V3, _OMISSION_NOTICE,
    _compact_id, _hash, _json, compact_records_for_chunk, compact_summary_batch,
    embedding_text, records_for_chunk, validate_compact_batch, validate_compact_chunk,
)
from tests.test_rag_compact import CompactModel, sections_fixture
from tests.test_rag_sections import SPEC, batch, event


def fixture_chunk(index=0, version=COMPACT_EVIDENCE_V1):
    original = batch([event(index)]).chunks[0]
    record = records_for_chunk(original)[0]
    metadata = {key: value for key, value in record.items() if key not in {"message", "fingerprint"}}
    values = {"active": False, "codes": ["A", 2, None], "correlation_id": "trace-λ",
              "event_id": 9007199254740993, "id": "original-alias", "integer": 1,
              "nothing": None, "numeric": 1.0, "object": {"flag": True, "n": 2.0}}
    fields = [{"key": key, "value": value, "event_refs": ["e0"]} for key, value in sorted(values.items())]
    evidence = _json({"version": version, "records": [{"ref": "e0", **metadata}],
                      "important_fields": fields, "uncertainties": [_OMISSION_NOTICE]})
    chunk = replace(original, summary="Synthetic operation reported.", summary_model="synthetic-model",
                    compression_version=COMPACT_VERSION, compact_evidence=evidence,
                    pattern_id=_hash([COMPACT_VERSION, "Synthetic operation reported.", fields]))
    return replace(chunk, chunk_id=_compact_id(chunk, evidence, chunk.summary, chunk.summary_model))


V1_GOLDEN = (
    '{"exact_metrics":{"duration_count":1,"duration_max_ms":0.5,"duration_min_ms":0.5,'
    '"duration_sum_ms":0.5,"event_count":1,"status_counts":{"401":1}},"important_fields":['
    '{"event_refs":["e0"],"key":"active","value":false},'
    '{"event_refs":["e0"],"key":"codes","value":["A",2,null]},'
    '{"event_refs":["e0"],"key":"correlation_id","value":"trace-λ"},'
    '{"event_refs":["e0"],"key":"event_id","value":9007199254740993},'
    '{"event_refs":["e0"],"key":"id","value":"original-alias"},'
    '{"event_refs":["e0"],"key":"integer","value":1},'
    '{"event_refs":["e0"],"key":"nothing","value":null},'
    '{"event_refs":["e0"],"key":"numeric","value":1.0},'
    '{"event_refs":["e0"],"key":"object","value":{"flag":true,"n":2.0}}],'
    '"model_summary":"Synthetic operation reported."}'
)


class CompactEmbeddingVersions(unittest.IsolatedAsyncioTestCase):
    def test_v1_golden_bytes_and_v2_omits_only_field_refs(self):
        v1, v2 = fixture_chunk(), fixture_chunk(version=COMPACT_EVIDENCE_V2)
        self.assertEqual(embedding_text(v1).encode(), V1_GOLDEN.encode())
        before = asdict(v2)
        old, new = validate_compact_chunk(v1), validate_compact_chunk(v2)
        self.assertEqual(_json({**old, "version": COMPACT_EVIDENCE_V2}), _json(new))
        expected = json.loads(V1_GOLDEN)
        expected["important_fields"] = [{"key": field["key"], "value": field["value"]}
                                        for field in expected["important_fields"]]
        self.assertEqual(embedding_text(v2).encode(), _json(expected).encode())
        self.assertEqual(asdict(v2), before)
        self.assertEqual(v1.pattern_id, v2.pattern_id)
        self.assertNotEqual(v1.chunk_id, v2.chunk_id)
        self.assertEqual(compact_records_for_chunk(v1), compact_records_for_chunk(v2))
        # Full typed evidence, metadata, bounds and loss notes differ only in the two allowed fields.
        self.assertEqual(asdict(replace(v1, chunk_id=v2.chunk_id, compact_evidence=v2.compact_evidence)), before)

    def test_unknown_versions_and_invalid_durable_bindings_fail_before_rendering(self):
        for version in ("compact-evidence-v3", "", None, [], 2):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "invalid_compact_evidence"):
                embedding_text(fixture_chunk(version=version))
        for version in (COMPACT_EVIDENCE_V1, COMPACT_EVIDENCE_V2):
            chunk = fixture_chunk(version=version)
            evidence = json.loads(chunk.compact_evidence)
            evidence["important_fields"][0]["event_refs"] = ["e999"]
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "invalid_compact_evidence"):
                embedding_text(replace(chunk, compact_evidence=_json(evidence)))

    async def test_new_generation_emits_v3_and_keeps_all_durable_refs(self):
        compact = await compact_summary_batch(await sections_fixture(), CompactModel())
        validate_compact_batch(compact)
        chunk = compact.chunks[0]
        evidence = validate_compact_chunk(chunk)
        self.assertEqual(evidence["version"], COMPACT_EVIDENCE_V3)
        self.assertTrue(all(field["event_refs"] for field in evidence["important_fields"]))
        self.assertEqual(_json(json.loads(embedding_text(chunk))["important_fields"]),
                         _json([{key: field[key] for key in ("key", "value")}
                                for field in evidence["important_fields"]]))

    async def test_mixed_durable_versions_roundtrip_and_embed_with_unchanged_spec(self):
        class Provider:
            local_only = preserve_content = True
            spec = SPEC

            async def embed(self, texts, **kwargs):
                self.texts = texts
                return [[1., 2., 3.] for _ in texts]

        mixed = replace(batch([event(), event(1)]), chunks=(fixture_chunk(), fixture_chunk(1, COMPACT_EVIDENCE_V2)))
        validate_compact_batch(mixed)
        decoded = _decode(_encode(mixed))
        self.assertEqual(decoded, mixed)
        provider = Provider()
        result = await embed_batch(decoded, provider)
        self.assertEqual(provider.texts, [embedding_text(chunk) for chunk in mixed.chunks])
        self.assertTrue(all(item.spec == SPEC for item in result.chunks))
        detail = next(stage.detail for stage in result.provenance if stage.stage == "embedding")
        self.assertIn("document_transports=compact-evidence-v1,compact-evidence-v2", detail)
