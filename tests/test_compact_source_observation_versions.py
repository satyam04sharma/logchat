"""Durable v3 dispatch controls using synthetic sources and mock embeddings."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from logchat.rag.scheduler import _decode, _encode
from logchat.rag.sections import (
    COMPACT_EVIDENCE_V3, _compact_id, _compact_pattern_id, _json,
    compact_records_for_chunk, embedding_text, upgrade_embedding_projection,
    validate_compact_chunk, validate_projection_upgrade,
)
from logchat.rag.source_observations import (
    RENDER_VERSION, EMBEDDING_VERSION, render_observations, select_observations,
)
from tests.test_compact_embedding_versions import fixture_chunk, V1_GOLDEN
from tests.test_rag_sections import batch, event


def v3_chunk():
    old = fixture_chunk()
    historical = json.loads(old.compact_evidence)
    records = historical['records']
    fields = historical['important_fields']
    views = {'e0': 'synthetic operation reported extra discarded body'}
    observations, _ = select_observations(
        {'source_phrases': [{'record_ref': 'e0', 'text': 'synthetic operation'}], 'important_fields': []},
        records, views, {'e0': [views['e0']]}, [], output_budget=8000)
    evidence = {'version': COMPACT_EVIDENCE_V3, 'render_version': RENDER_VERSION,
                'records': records, 'important_fields': fields, 'source_observations': observations}
    summary = render_observations(observations, records, fields)
    chunk = replace(old, compact_evidence=_json(evidence), summary=summary,
                    pattern_id=_compact_pattern_id(evidence, summary))
    return replace(chunk, chunk_id=_compact_id(chunk, chunk.compact_evidence, summary, chunk.summary_model))


def resigned(chunk, evidence, summary=None):
    summary = chunk.summary if summary is None else summary
    changed = replace(chunk, compact_evidence=_json(evidence), summary=summary,
                      pattern_id=_compact_pattern_id(evidence, summary))
    return replace(changed, chunk_id=_compact_id(changed, changed.compact_evidence, summary, changed.summary_model))


def test_v3_canonical_dispatch_no_ref_duplication_or_discarded_body():
    chunk = v3_chunk()
    evidence = validate_compact_chunk(chunk)
    transport = embedding_text(chunk)
    observation = evidence['source_observations'][0]
    expected = _json({'evidence_version': COMPACT_EVIDENCE_V3, 'render_version': RENDER_VERSION,
        'embedding_version': EMBEDDING_VERSION, 'summary': chunk.summary,
        'source_observations': [{key: observation[key] for key in ('text', 'exact_metrics', 'observed_window')}],
        'whole_section_facts': {key: observation[key] for key in ('exact_metrics', 'observed_window')},
        'important_fields': [{'key': f['key'], 'value': f['value']} for f in evidence['important_fields']],
        'measurement_notice': 'Status counts are reported responses; missing measurements are unknown. '
            'duration_sum_ms is summed measured work, not elapsed time; observed_window.span_ms is timestamp span.'})
    assert transport.encode() == expected.encode()
    assert 'event_refs' not in transport and 'record_ref' not in transport
    assert 'discarded body' not in chunk.compact_evidence + chunk.summary + transport
    assert '1 retained events contain source phrase' in transport
    assert compact_records_for_chunk(chunk)[0]['important_fields'][0] == {'key': 'active', 'value': False}
    assert embedding_text(fixture_chunk()).encode() == V1_GOLDEN.encode()


def test_final_v3_renderer_embedding_and_identity_goldens():
    chunk = v3_chunk()
    assert RENDER_VERSION == 'source-observation-render-v2'
    assert EMBEDDING_VERSION == 'source-observation-embedding-v1'
    assert chunk.chunk_id == 'ed9203454135c8c7b383ada24c6d498c580dc008e62246ac5a9bd4dcce926802'
    assert chunk.pattern_id == '1376891b81d781c8bc29e81b6b0edcf749c135e194c34fe6e2ec8489a7c19d13'
    assert hashlib.sha256(embedding_text(chunk).encode()).hexdigest() == (
        '4e33b764fd4163c9cf143b5a38a23fc68bc6de57008f418ca093227f61643f72')


@pytest.mark.parametrize('mutation', ['count', 'status', 'duration', 'span', 'member', 'witness',
    'renderer', 'extra', 'uncertainties', 'summary'])
def test_invalid_durable_facts_reject_even_with_recomputed_identity(mutation):
    chunk = v3_chunk()
    evidence = deepcopy(validate_compact_chunk(chunk))
    observation = evidence['source_observations'][0]
    summary = chunk.summary
    if mutation == 'count':
        observation['exact_metrics']['event_count'] = 17
    elif mutation == 'status':
        observation['exact_metrics']['status_counts'] = {'504': 1}
    elif mutation == 'duration':
        observation['exact_metrics']['duration_sum_ms'] = 5000
    elif mutation == 'span':
        observation['observed_window']['span_ms'] = 100
    elif mutation == 'member':
        observation['event_refs'] = ['e99']
    elif mutation == 'witness':
        observation['record_ref'] = 'e99'
    elif mutation == 'renderer':
        evidence['render_version'] = 'unknown-renderer'
    elif mutation == 'extra':
        observation['cause'] = 'invented'
    elif mutation == 'uncertainties':
        evidence['uncertainties'] = ['invented prose']
    else:
        summary += '\n17 failures'
    with pytest.raises(ValueError, match='invalid_compact_evidence'):
        embedding_text(resigned(chunk, evidence, summary))


def test_typed_field_change_is_bound_by_identity_and_embedding():
    chunk = v3_chunk()
    evidence = deepcopy(validate_compact_chunk(chunk))
    evidence['important_fields'][0]['value'] = 0
    with pytest.raises(ValueError, match='compact_identity_mismatch'):
        embedding_text(replace(chunk, compact_evidence=_json(evidence)))
    # Once source is discarded, a re-signed valid typed field is structurally
    # valid; its exact type must still change identity and semantic transport.
    changed = resigned(chunk, evidence)
    assert changed.chunk_id != chunk.chunk_id and changed.pattern_id != chunk.pattern_id
    assert json.loads(embedding_text(chunk))['important_fields'][0]['value'] is False
    assert type(json.loads(embedding_text(changed))['important_fields'][0]['value']) is int


def test_immutable_identity_detects_phrase_change_that_discarded_source_cannot_recheck():
    chunk = v3_chunk()
    evidence = deepcopy(validate_compact_chunk(chunk))
    evidence['source_observations'][0]['text'] = 'changed phrase'
    summary = render_observations(evidence['source_observations'], evidence['records'], evidence['important_fields'])
    for changed in (replace(chunk, compact_evidence=_json(evidence), summary=summary),
                    replace(chunk, chunk_id='0' * 64), replace(chunk, pattern_id='0' * 64)):
        with pytest.raises(ValueError, match='compact_identity_mismatch'):
            validate_compact_chunk(changed)
    # Re-signed data is structurally verifiable, not source re-authenticated.
    assert validate_compact_chunk(resigned(chunk, evidence, summary))['source_observations'][0]['text'] == 'changed phrase'


def test_v3_roundtrip_projection_noop_and_no_source_free_migration():
    current = replace(batch([event()]), chunks=(v3_chunk(),))
    assert _decode(_encode(current)) == current
    assert upgrade_embedding_projection(current) == current
    old = replace(current, chunks=(fixture_chunk(),))
    with pytest.raises(ValueError, match='projection_upgrade_content_mismatch'):
        validate_projection_upgrade(old, current)
    assert upgrade_embedding_projection(old).chunks[0].summary == old.chunks[0].summary


def test_historical_identity_goldens_and_v3_full_fields_fit_summary():
    from logchat.rag.sections import COMPACT_EVIDENCE_V2
    old = fixture_chunk()
    v2 = fixture_chunk(version=COMPACT_EVIDENCE_V2)
    assert old.chunk_id == '531ce2d024668e5e9245785cb1398c0e9711d94a09ddb2caad64d4ca4a2609ab'
    assert v2.chunk_id == '0c9c74cc26242635d10c810d2d36e17b3532bcf33de40ca4ce214f418df320e6'
    assert old.pattern_id == v2.pattern_id == 'b707044a0a5b4ad02422e86b66e10c240332e98ee64f7b30c238cc151e65eeb2'
    current = v3_chunk()
    evidence = validate_compact_chunk(current)
    evidence['important_fields'] = validate_compact_chunk(old)['important_fields']
    summary = render_observations(evidence['source_observations'], evidence['records'], evidence['important_fields'])
    assert len(_json(summary)) <= 1600
    assert validate_compact_chunk(resigned(current, evidence, summary))['important_fields'] == evidence['important_fields']
    assert len(evidence['important_fields']) == 9


@pytest.mark.asyncio
async def test_v3_mock_embedding_preserves_spec_and_declares_transport():
    from logchat.rag.builder import embed_batch
    from tests.test_rag_sections import SPEC

    class Provider:
        local_only = preserve_content = True
        spec = SPEC

        async def embed(self, texts, **kwargs):
            self.texts = texts
            return [[1., 2., 3.] for _ in texts]

    current = replace(batch([event()]), chunks=(v3_chunk(),))
    provider = Provider()
    result = await embed_batch(current, provider)
    assert provider.texts == [embedding_text(current.chunks[0])]
    assert result.chunks[0].spec == SPEC
    assert 'document_transports=compact-evidence-v3' in next(
        stage.detail for stage in result.provenance if stage.stage == 'embedding')
