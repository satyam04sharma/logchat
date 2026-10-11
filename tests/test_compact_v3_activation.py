"""Actual fresh compaction seam and active consumers, with deterministic models."""
from logchat.rag.compact_input import expand_compact_input
from tests.physical_preflight import SyntheticPhysicalPreflight
from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from logchat.rag.answering import assemble_context
from logchat.rag.contracts import RetrievalCell
from logchat.rag.remapper import RemapConfig, remap
from logchat.rag.retriever import retrieve
from logchat.rag.sections import (
    COMPACT_EVIDENCE_V3, COMPACT_EVIDENCE_V1, COMPACT_EVIDENCE_V2, SummaryConfig,
    SectionModelError, _compact_feasible, _compact_request, _compact_id, _compact_pattern_id,
    _json, active_compact_context, compact_summary_batch, compact_records_for_chunk,
    embedding_text, records_for_chunk, section_batch, validate_compact_chunk, validate_refinement,
)
from logchat.rag.source_observations import _facts
from tests.test_rag_sections import batch, event, LocalPartitionModel, SPEC
from tests.test_compact_embedding_versions import fixture_chunk
from tests.test_rag_compact_summary_context import Backend, Provider, Model


class Selector(SyntheticPhysicalPreflight):
    local_only = preserve_content = True
    chat_model = 'synthetic-selector'

    def __init__(self, phrases, fields=False):
        self.phrases, self.fields, self.calls = phrases, fields, []

    async def generate(self, instruction, payload, schema):
        self.calls.append((instruction, deepcopy(payload), deepcopy(schema)))
        from tests.catalog_selector import select_topics
        assert set(schema['properties']) == {'selections', 'important_fields'}
        assert 'serialized' in instruction and '1600' in instruction
        return select_topics(payload, self.phrases, fields=self.fields, allow_absent_topics=True)



async def fresh(events, selector):
    source = await section_batch(batch(events), LocalPartitionModel())
    compact = await compact_summary_batch(source, selector)
    validate_refinement(source, compact)
    return source, compact


@pytest.mark.asyncio
@pytest.mark.parametrize('first', [11, 24])
async def test_35_record_actual_route_membership_and_complete_reservations(first):
    selector = Selector(['workerEnv.init()', 'mod_jk child init'])
    source, compact = await fresh([event(i, ('workerEnv.init()' if i < first else 'mod_jk child init')
                                      + ' trailing private detail') for i in range(35)], selector)
    assert len(source.chunks) == len(compact.chunks) == len(selector.calls) == 1
    evidence = validate_compact_chunk(compact.chunks[0])
    assert evidence['version'] == COMPACT_EVIDENCE_V3
    assert [o['exact_metrics']['event_count'] for o in evidence['source_observations']] == [first, 35-first]
    for o in evidence['source_observations']:
        expected = [row['ref'] for row in expand_compact_input(selector.calls[0][1])['records'] if o['text'] in row['message']]
        assert o['event_refs'] == expected
        assert {k: o[k] for k in ('exact_metrics', 'observed_window')} == _facts([
            row for row in evidence['records'] if row['ref'] in expected])
    records = records_for_chunk(source.chunks[0])
    from logchat.rag.source_catalog import build_source_catalog
    views = {r['event_key']: r['message'] for r in records}
    bodies = {key: [text] for key, text in views.items()}
    options = dict(source_catalog=build_source_catalog(views, bodies), protected_bodies=bodies)
    payload, schema, _, _ = _compact_request(source.chunks[0], records, SummaryConfig(), **options)
    assert selector.calls[0][1:] == (payload, schema)
    bounds = _compact_feasible(source.chunks[0], records, SummaryConfig(), **options)
    for value, kind in ((compact.chunks[0].compact_evidence, 'evidence'),
                        (embedding_text(compact.chunks[0]), 'embedding')):
        assert len(value) <= bounds[kind + '_chars']
        assert len(value.encode()) <= bounds[kind + '_bytes']
    assert 'trailing private detail' not in json.dumps(asdict(compact), default=str)


@pytest.mark.asyncio
async def test_33_record_incident_has_only_its_504_and_5000ms():
    _, compact = await fresh([event(i, ('incident reported' if i == 0 else 'directory reported') + ' private tail',
        request_status=504 if i == 0 else 200, duration_ms=5000 if i == 0 else None) for i in range(33)],
        Selector(['incident', 'directory']))
    assert len(compact.chunks) == 1
    one, rest = validate_compact_chunk(compact.chunks[0])['source_observations']
    assert one['exact_metrics']['status_counts'] == {'504': 1}
    assert one['exact_metrics']['duration_sum_ms'] == 5000
    assert rest['exact_metrics']['status_counts'] == {'200': 32}
    assert rest['exact_metrics']['duration_count'] == 0
    assert rest['exact_metrics']['duration_min_ms'] is None


@pytest.mark.asyncio
async def test_overlap_repetition_causal_source_numbers_are_quotes_not_generated_claims():
    _, compact = await fresh([event(8, 'operation operation 17 caused outage private tail', duration_ms=None, request_status=None),
                             event(2, 'operation 17 caused outage another tail', duration_ms=1e-7)],
                            Selector(['operation', 'operation 17', '17 caused outage']))
    chunk = compact.chunks[0]
    evidence = validate_compact_chunk(chunk)
    assert [len(o['event_refs']) for o in evidence['source_observations']] == [2, 2, 2]
    assert all(o['exact_metrics']['duration_count'] == 1 for o in evidence['source_observations'])
    assert all(o['observed_window']['span_ms'] == 6000 for o in evidence['source_observations'])
    assert 'Memberships may overlap; do not add counts.' in chunk.summary
    assert '"17 caused outage" (source quote)' in chunk.summary
    assert chunk.metrics.event_count == 2


@pytest.mark.asyncio
async def test_atomic_union_across_packed_chunks_includes_nonwitness_body(monkeypatch):
    # 33 required correlations per record force separate packed requests. Each
    # local selection is safe, but their union reconstructs the third record.
    from connectors.local import parse_push_events
    from tests.test_grouping_projection import prepare, sidecar
    normalized = parse_push_events([{'event_id': f'item-{i}', 'message': text,
        'timestamp': '2026-10-10T00:00:00Z', **{f'part_{i}_{j}_id': j for j in range(33)}}
        for i, text in enumerate(['alpha extra', 'beta extra', 'alpha beta'])], preserve_fields=True)
    source = await section_batch(prepare(normalized), LocalPartitionModel(), model_events=sidecar(normalized))

    class Across(Selector):
        async def generate(self, instruction, payload, schema):
            self.calls.append(payload)
            text = expand_compact_input(payload)['records'][0]['message']
            chosen = 'alpha' if text.startswith('alpha extra') else 'beta' if text.startswith('beta extra') else None
            return {'source_phrases': [{'record_ref': 'e0', 'text': chosen}] if chosen else [], 'important_fields': []}

    # Corrupt the trusted decoder to exercise the independent final guard;
    # normal catalog dispatch cannot select this deliberately unsafe union.
    from logchat.rag.source_observations import select_observations
    def corrupt(value, records, views, bodies, fields, catalog, *, output_budget):
        return select_observations(value, records, views, bodies, fields, output_budget=output_budget)
    monkeypatch.setattr('logchat.rag.source_catalog.select_catalog_observations', corrupt)
    selector = Across([])
    with pytest.raises(SectionModelError, match='compact_invalid_output'):
        await compact_summary_batch(source, selector, model_events=sidecar(normalized))
    assert len(selector.calls) == 3
    assert all(chunk.compact_evidence is None for chunk in source.chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [COMPACT_EVIDENCE_V1, COMPACT_EVIDENCE_V2, COMPACT_EVIDENCE_V3])
async def test_active_classifier_evidence_preview_answer_and_agent_context(version):
    canary = 'FALSE_PROSE_CANARY_17_incidents_caused_total_outage'
    if version == COMPACT_EVIDENCE_V3:
        _, compact = await fresh([event(message='synthetic operation private detail')], Selector(['synthetic operation']))
        chunk = compact.chunks[0]
    else:
        chunk = fixture_chunk(version=version)
        evidence = validate_compact_chunk(chunk)
        evidence['uncertainties'].append(canary)
        chunk = replace(chunk, summary=canary, compact_evidence=_json(evidence),
                        pattern_id=_compact_pattern_id(evidence, canary))
        chunk = replace(chunk, chunk_id=_compact_id(chunk, chunk.compact_evidence, canary, chunk.summary_model))
    before = (asdict(chunk), embedding_text(chunk))
    identity = chunk.identity
    cell = RetrievalCell('current', identity.owner_id, identity.project_id, identity.environment_id,
                         chunk.window, source_ids=(identity.source_id,))
    model = Model()
    found = await retrieve('What operation was reported?', (cell,), Backend(chunk), Provider(), preserve_content=True)
    checked = await remap(found, model, config=RemapConfig(input_character_budget=24000, input_byte_budget=48000),
                         preserve_content=True)
    assert len(model.calls) == 1 and len(checked.selected) == 1
    result = assemble_context(checked, preserve_content=True)
    assert canary not in _json(model.calls) and canary not in _json(result)
    assert result['cited_evidence_ids'] == [chunk.chunk_id]
    active = active_compact_context(chunk)
    assert result['evidence'][0]['retained_events'] == active['retained_events']
    assert result['candidate_evidence'][0]['important_fields'] == active['important_fields']
    if version == COMPACT_EVIDENCE_V3:
        assert result['agent_context']['evidence'][0]['source_observations'] == active['source_observations']
        assert '1 retained events contain source phrase' in result['answer']
        assert model.calls[0][1]['candidate']['source_observations'] == active['source_observations']
    else:
        assert canary in json.dumps(asdict(chunk), default=str) and canary in embedding_text(chunk)
        assert 'Source-derived operation observations and counts are unavailable' in result['answer']
    assert (asdict(chunk), embedding_text(chunk)) == before


@pytest.mark.asyncio
async def test_reserves_full_optional_union_independently_for_chars_and_utf8():
    # More than 64 choices, 10 mandatory fields, and large optional strings whose
    # largest UTF-8 costs differ from their largest character costs.
    events = []
    for i in range(2):
        values = {f'correlation_{i}_{j}_id': j for j in range(5)}
        values.update({f'optional_{i}_{j}': ('😀' * 25 if j % 2 else '\\' * 40) for j in range(30)})
        events.append(event(i, 'typed operation private tail ' + json.dumps(values, ensure_ascii=False)))
    source = await section_batch(batch(events), LocalPartitionModel())
    assert len(source.chunks) == 1
    chunk = source.chunks[0]
    payload, _, metadata, _ = _compact_request(chunk, records_for_chunk(chunk), SummaryConfig())
    from logchat.rag.compact_bounds import compact_reservations
    bounds = compact_reservations(payload, metadata, SummaryConfig())
    required = payload['required_field_refs']
    assert len(required) == 10 and len(expand_compact_input(payload)['field_catalog']) == 70

    class Maximal(Selector):
        async def generate(self, instruction, payload, schema):
            value = await super().generate(instruction, payload, schema)
            optional = [f for f in expand_compact_input(payload)['field_catalog'] if f['field_ref'] not in required]
            optional.sort(key=lambda f: len(_json(f).encode()) if self.fields else len(_json(f)), reverse=True)
            value['important_fields'] = required + [f['field_ref'] for f in optional[:54]]
            return value

    for byte in (False, True):
        compact = await compact_summary_batch(source, Maximal(['typed operation', 'operation', 'typed'], fields=byte))
        assert len(compact.chunks) == 1
        evidence = validate_compact_chunk(compact.chunks[0])
        assert len(evidence['important_fields']) == 64
        for text, name in ((compact.chunks[0].compact_evidence, 'evidence'),
                           (embedding_text(compact.chunks[0]), 'embedding')):
            assert len(text) <= bounds[name + '_chars']
            assert len(text.encode()) <= bounds[name + '_bytes']
    # Bound the largest permissible union, not 64 optional PLUS required fields.
    assert bounds['evidence_chars'] <= 16000


@pytest.mark.asyncio
async def test_escaped_phrase_set_rejects_rendering_without_repair_or_source_mutation(monkeypatch):
    phrases = [chr(i) * 120 for i in (1, 2, 3, 4)]
    source = await section_batch(batch([event(i, phrase + ' omitted content') for i, phrase in enumerate(phrases)]), LocalPartitionModel())
    assert len(source.chunks) == 1
    # Preflight reserves 1600 for accepted summaries: this source is feasible,
    # although the chosen four heavily escaped phrases fail the final predicate.
    _compact_feasible(source.chunks[0], records_for_chunk(source.chunks[0]), SummaryConfig())
    # Historical free-text seam remains strict, independent of active grammar.
    from logchat.rag.source_observations import select_observations
    def historical(value, records, views, bodies, fields, catalog, *, output_budget):
        return select_observations(value, records, views, bodies, fields, output_budget=output_budget)
    monkeypatch.setattr('logchat.rag.source_catalog.select_catalog_observations', historical)
    class Historical(Selector):
        async def generate(self, instruction, payload, schema):
            self.calls.append(payload)
            return {'source_phrases': [{'record_ref': f'e{i}', 'text': text}
                                      for i, text in enumerate(phrases)], 'important_fields': []}
    selector = Historical(phrases)
    with pytest.raises(SectionModelError, match='compact_invalid_output'):
        await compact_summary_batch(source, selector)
    assert len(selector.calls) == 1
    assert source.chunks[0].compact_evidence is None


@pytest.mark.asyncio
async def test_impossible_late_single_calls_no_model_and_complete_pack_is_atomic():
    source = await section_batch(batch([event(0, 'safe operation private detail'),
        event(1, 'operation ' + json.dumps({f'resource_{i}_id': i for i in range(65)}))]), LocalPartitionModel())
    selector = Selector(['operation'])
    with pytest.raises(SectionModelError, match='compact_input_budget_exceeded'):
        await compact_summary_batch(source, selector)
    assert selector.calls == []

    source = await section_batch(batch([event(i, 'safe operation private detail ' + json.dumps({
        f'resource_{i}_{j}_id': j for j in range(33)})) for i in range(3)]), LocalPartitionModel())

    class LateFailure(Selector):
        async def generate(self, instruction, payload, schema):
            result = await super().generate(instruction, payload, schema)
            if len(self.calls) == 3:
                result['summary'] = 'forbidden late prose'
            return result

    selector = LateFailure(['operation'])
    with pytest.raises(SectionModelError, match='compact_invalid_output'):
        await compact_summary_batch(source, selector)
    assert len(selector.calls) == 3
    assert all(c.compact_evidence is None for c in source.chunks)
    actual = await compact_summary_batch(source, Selector(['operation']))
    assert len(actual.chunks) == 3
    assert {r['event_key'] for c in actual.chunks for r in compact_records_for_chunk(c)} == set(source.event_keys)
    validate_refinement(source, actual)
