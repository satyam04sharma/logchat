"""Offline narrow-cell controls using actual compact intake and query seams."""
from dataclasses import asdict, replace
from datetime import timedelta, timezone

import pytest

from logchat.rag.answering import assemble_context
from logchat.rag.contracts import Candidate, RetrievalCell, TimeWindow
from logchat.rag.record_view import restore_records
from logchat.rag.remapper import RemapConfig, remap
from logchat.rag.retriever import retrieve
from logchat.rag.sections import (
    COMPACT_EVIDENCE_V1, COMPACT_EVIDENCE_V2, _compact_id, _compact_pattern_id,
    _json, active_compact_context, embedding_text, validate_compact_chunk,
)
from tests.test_compact_v3_activation import Selector, fresh
from tests.test_rag_compact_summary_context import Backend, Model, Provider
from tests.test_rag_sections import NOW, event

CONFIG = RemapConfig(input_character_budget=24000, input_byte_budget=48000)


def cell(chunk, start, end, name='current'):
    identity = chunk.identity
    return RetrievalCell(name, identity.owner_id, identity.project_id, identity.environment_id,
        TimeWindow(NOW + timedelta(seconds=start), NOW + timedelta(seconds=end)),
        source_ids=(identity.source_id,), label=name)


def immutable(chunk):
    return _json(asdict(chunk) | {'window': {'start': chunk.window.start.isoformat(),
        'end': chunk.window.end.isoformat()}, 'first_event_at': chunk.first_event_at.isoformat(),
        'last_event_at': chunk.last_event_at.isoformat()}), chunk.compact_evidence, embedding_text(chunk)


async def query(chunks, cells, question='What operation was reported?'):
    model = Model()
    found = await retrieve(question, cells, Backend(*chunks), Provider(), preserve_content=True)
    checked = await remap(found, model, config=CONFIG, preserve_content=True)
    return model, checked, assemble_context(checked, preserve_content=True)


async def incident_chunk():
    _, compact = await fresh([
        event(0, 'incident operation code=SDK-420 shared=7 outside=true private tail',
              request_status=504, duration_ms=5000),
        event(30, 'healthy operation code=SDK-421 shared=7 inside=false private tail',
              request_status=200, duration_ms=2),
        event(31, 'excluded-end private tail', request_status=503, duration_ms=9000),
    ], Selector(['incident', 'healthy', 'operation', 'excluded-end'], fields=True))
    assert len(compact.chunks) == 1
    return compact.chunks[0]


@pytest.mark.asyncio
async def test_narrow_projection_all_surfaces_and_immutable_bytes():
    chunk = await incident_chunk()
    before = immutable(chunk)
    scoped_cell = cell(chunk, 29, 31)
    model, checked, result = await query((chunk,), (scoped_cell,))
    assert len(model.calls) == len(checked.selected) == 1
    active = active_compact_context(chunk, scoped_cell)
    assert [row['ref'] for row in active['retained_events']] == ['e1']
    assert active['retained_events'][0]['event_id'] == 'customer-event-30'
    assert active['exact_metrics']['status_counts'] == {'200': 1}
    assert active['exact_metrics']['duration_sum_ms'] == 2
    assert [item['text'] for item in active['source_observations']] == ['healthy', 'operation']
    operation = active['source_observations'][1]
    assert operation['event_refs'] == ['e1']
    assert operation['record_ref'] == 'e1' and operation['origin_record_ref'] == 'e0'
    assert operation['exact_metrics']['event_count'] == 1
    assert all(field['event_refs'] == ['e1'] for field in active['important_fields'])
    assert any(field['key'] == 'shared' and type(field['value']) is int and field['value'] == 7
               for field in active['important_fields'])
    assert any(field['key'] == 'inside' and field['value'] is False for field in active['important_fields'])
    # Recursive check covers evidence, preview, findings, answer, observations,
    # agent context, all nested metrics and classifier transport together.
    wire = _json(result) + _json(model.calls)
    for excluded in ('incident', 'SDK-420', 'excluded-end', 'customer-event-0',
                     'customer-event-31', '504', '503', '5000', '9000', '"outside"'):
        # Hashes are opaque provenance; check quoted strings/numeric values
        # separately below rather than assuming random hash digit absence.
        if excluded not in ('504', '503', '5000', '9000'):
            assert excluded not in wire
    def check_metrics(value):
        if isinstance(value, dict):
            if 'status_counts' in value:
                assert value['status_counts'] == {'200': 1}
            if 'duration_sum_ms' in value:
                assert value['duration_sum_ms'] == 2
            for child in value.values():
                check_metrics(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                check_metrics(child)
    check_metrics(result)
    check_metrics(model.calls)
    row = result['evidence'][0]
    assert (row['cell_id'], row['original_chunk_id']) == ('current', chunk.chunk_id)
    assert row['window']['start'] == chunk.window.start.isoformat()
    assert row['query_window']['start'] == scoped_cell.window.start.isoformat()
    assert row['event_count'] == result['plan']['cells'][0]['metrics']['events'] == 1
    assert '1 retained events contain source phrase "operation"' in result['answer']
    assert immutable(chunk) == before


@pytest.mark.asyncio
async def test_half_open_endpoints_and_timezone_equivalence():
    chunk = await incident_chunk()
    scoped_cell = cell(chunk, 30, 31)
    offset = timezone(timedelta(hours=-4))
    equivalent = replace(scoped_cell, window=TimeWindow(
        scoped_cell.window.start.astimezone(offset), scoped_cell.window.end.astimezone(offset)))
    assert active_compact_context(chunk, scoped_cell) == active_compact_context(chunk, equivalent)
    assert [r['ref'] for r in active_compact_context(chunk, scoped_cell)['retained_events']] == ['e1']
    assert [r['ref'] for r in active_compact_context(chunk, cell(chunk, 31, 32))['retained_events']] == ['e2']


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['Inspect SDK-420', 'SDK-420'])
async def test_outside_strong_identifier_has_zero_model_admission(question):
    chunk = await incident_chunk()
    model, checked, result = await query((chunk,), (cell(chunk, 29, 31),), question)
    assert model.calls == [] and checked.selected == ()
    assert result['evidence'] == []
    assert 'incident' not in _json(result)


@pytest.mark.asyncio
async def test_matching_index_is_scoped_order_not_original_ref_number():
    chunk = await incident_chunk()
    model, _, _ = await query((chunk,), (cell(chunk, 29, 31),), 'Inspect SDK-421')
    payload = model.calls[0][1]['candidate']
    assert payload['literal_matching_record_indexes'] == [0]
    assert restore_records(payload['records'])[0]['ref'] == 'e1'


@pytest.mark.asyncio
async def test_empty_records_and_empty_observations_keep_real_fields_only():
    _, compact = await fresh([event(0, 'incident private tail'),
        event(30, 'healthy code=SDK-421 private tail', duration_ms=2, request_status=200)],
        Selector(['incident']))
    chunk = compact.chunks[0]
    model, checked, result = await query((chunk,), (cell(chunk, 29, 31),), 'Inspect SDK-421')
    assert len(checked.selected) == len(model.calls) == 1
    assert result['evidence'][0]['source_observations'] == []
    assert any(f['value'] == 'SDK-421' for f in result['evidence'][0]['important_fields'])
    assert 'incident' not in _json(result) + _json(model.calls)
    empty = cell(chunk, 31, 32)
    active = active_compact_context(chunk, empty)
    assert active['retained_events'] == active['important_fields'] == active['source_observations'] == []
    assert active['observed_window'] is None
    model, checked, result = await query((chunk,), (empty,))
    assert not model.calls and not checked.selected and not result['candidate_evidence']
    # Defense also applies when a backend bypasses retrieval's initial gate.
    found = replace(checked.retrieval, candidates=(Candidate(empty.cell_id, chunk, .5),))
    model = Model()
    assert not (await remap(found, model, config=CONFIG, preserve_content=True)).selected
    assert not model.calls


@pytest.mark.asyncio
async def test_same_chunk_distinct_overlapping_cells_and_aggregate_identity_dedup():
    chunk = await incident_chunk()
    # A second valid compact representation shares the same canonical events.
    _, other = await fresh([
        event(0, 'incident operation code=SDK-420 shared=7 outside=true private tail', request_status=504, duration_ms=5000),
        event(30, 'healthy operation code=SDK-421 shared=7 inside=false private tail', request_status=200, duration_ms=2),
        event(31, 'excluded-end private tail', request_status=503, duration_ms=9000),
    ], Selector(['healthy']))
    second = other.chunks[0]
    assert second.chunk_id != chunk.chunk_id
    before = [immutable(c) for c in (chunk, second)]
    cells = (cell(chunk, 29, 31, 'narrow'), cell(chunk, 0, 31, 'overlap'))
    model, checked, result = await query((chunk, second), cells)
    assert len(model.calls) == len(checked.selected) == 4
    views = {(r['cell_id'], r['id']): r for r in result['evidence']}
    assert len(views) == 4
    assert views[('narrow', chunk.chunk_id)]['event_count'] == 1
    assert views[('overlap', chunk.chunk_id)]['event_count'] == 2
    assert [c['metrics']['events'] for c in result['plan']['cells']] == [1, 2]
    assert [c['metrics']['duration_sum_ms'] for c in result['plan']['cells']] == [2, 5002]
    assert len(result['citations']) == len(result['cited_evidence_ids']) == 2
    assert all(c['cell_ids'] == ['narrow', 'overlap'] for c in result['citations'])
    comparison = result['agent_context']['comparisons'][0]
    assert comparison['status'] == 'incompatible_scopes'
    assert comparison['left_minus_right_observed_event_count'] is None
    assert [immutable(c) for c in (chunk, second)] == before


@pytest.mark.asyncio
async def test_equal_disjoint_cells_compare_only_scoped_counts():
    chunk = await incident_chunk()
    _, _, result = await query((chunk,), (cell(chunk, 0, 1, 'earlier'), cell(chunk, 30, 31, 'later')))
    assert [c['metrics']['events'] for c in result['plan']['cells']] == [1, 1]
    comparison = result['agent_context']['comparisons'][0]
    assert comparison['status'] == 'selected_evidence_comparison'
    assert comparison['left_minus_right_observed_event_count'] == 0
    assert comparison['comparable_complete_population'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [COMPACT_EVIDENCE_V1, COMPACT_EVIDENCE_V2])
async def test_historical_narrow_canary_exclusion_and_immutable_recipe(version):
    chunk = await incident_chunk()
    evidence = validate_compact_chunk(chunk)
    from logchat.rag.sections import _OMISSION_NOTICE
    evidence = {k: evidence[k] for k in ('records', 'important_fields')}
    canary = 'HISTORICAL_UNCHECKED_INCIDENT_CANARY'
    evidence.update(version=version, uncertainties=[_OMISSION_NOTICE, canary])
    chunk = replace(chunk, summary=canary, compact_evidence=_json(evidence),
                    pattern_id=_compact_pattern_id(evidence, canary))
    chunk = replace(chunk, chunk_id=_compact_id(chunk, chunk.compact_evidence, canary, chunk.summary_model))
    before = immutable(chunk)
    model, _, result = await query((chunk,), (cell(chunk, 29, 31),))
    assert len(model.calls) == 1
    assert canary not in _json(result) + _json(model.calls)
    assert result['evidence'][0]['event_count'] == 1
    assert result['evidence'][0]['source_observations'] == []
    assert immutable(chunk) == before


@pytest.mark.asyncio
async def test_offset_spelled_durable_timestamps_filter_by_instant():
    from datetime import datetime
    from logchat.rag.source_observations import _facts

    original = await incident_chunk()
    evidence = validate_compact_chunk(original)
    # Synthetic durable encoding compatibility, not renewed source authentication.
    for row, hours in zip(evidence['records'], (9, -7, 5)):
        row['timestamp'] = datetime.fromisoformat(row['timestamp']).astimezone(
            timezone(timedelta(hours=hours))).isoformat()
    for observation in evidence['source_observations']:
        observation.update(_facts([r for r in evidence['records'] if r['ref'] in observation['event_refs']]))
    chunk = replace(original, compact_evidence=_json(evidence),
                    pattern_id=_compact_pattern_id(evidence, original.summary))
    chunk = replace(chunk, chunk_id=_compact_id(chunk, chunk.compact_evidence, chunk.summary, chunk.summary_model))
    before = immutable(chunk)
    model, _, result = await query((chunk,), (cell(chunk, 30, 31),))
    row = result['evidence'][0]
    assert [r['ref'] for r in row['retained_events']] == ['e1']
    assert row['retained_events'][0]['timestamp'].endswith('-07:00')
    assert row['exact_metrics']['status_counts'] == {'200': 1}
    assert 'incident' not in _json(result) + _json(model.calls)
    assert immutable(chunk) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['renumber', 'identity', 'typed_measurement', 'duplicate', 'reorder', 'invent'])
async def test_derived_members_cannot_change_durable_identity_or_values(mutation):
    from copy import deepcopy
    from logchat.rag.source_observations import scoped_observations

    chunk = await incident_chunk()
    before = immutable(chunk)
    evidence = validate_compact_chunk(chunk)
    rows = deepcopy(evidence['records'][1:])
    if mutation == 'renumber':
        rows[0]['ref'] = 'e0'
    elif mutation == 'identity':
        rows[0]['event_key'] = evidence['records'][0]['event_key']
    elif mutation == 'typed_measurement':
        rows[0]['duration_ms'] = '2'
    elif mutation == 'duplicate':
        rows.append(deepcopy(rows[0]))
    elif mutation == 'reorder':
        rows.reverse()
    else:
        rows[0]['ref'] = 'e99'
    with pytest.raises(ValueError, match='scoped_observation_record_mismatch'):
        scoped_observations(evidence, rows)
    assert immutable(chunk) == before


@pytest.mark.asyncio
async def test_outside_durable_corruption_is_not_hidden_by_projection():
    chunk = await incident_chunk()
    evidence = validate_compact_chunk(chunk)
    evidence['source_observations'][0]['exact_metrics']['duration_sum_ms'] = 1
    invalid = replace(chunk, compact_evidence=_json(evidence))
    # Even self-consistent IDs cannot make invalid stored facts pass validation.
    invalid = replace(invalid, pattern_id=_compact_pattern_id(evidence, invalid.summary),
        chunk_id=_compact_id(invalid, invalid.compact_evidence, invalid.summary, invalid.summary_model))
    with pytest.raises(ValueError, match='invalid_compact_evidence'):
        active_compact_context(invalid, cell(chunk, 29, 31))
    model, checked, result = await query((invalid,), (cell(chunk, 29, 31),))
    assert not model.calls and not checked.selected and not result['candidate_evidence']


@pytest.mark.asyncio
async def test_narrow_source_fairness_excludes_outside_only_id_before_admission():
    from logchat.rag.builder import prepare_preserved_batch
    from logchat.rag.sections import compact_summary_batch, section_batch
    from tests.test_rag_sections import IDENTITY, WINDOW, LocalPartitionModel

    chunks = []
    # A high-ranked source has only an outside-window ID. Twelve dominant
    # candidates must not crowd the two other eligible services out of six calls.
    for index, service in enumerate(['outside'] + ['web'] * 12 + ['worker', 'auth']):
        identity = replace(IDENTITY, source_id=service)
        events = [event(0, 'incident code=SDK-420 private tail', service=service),
                  event(30, 'healthy code=' + ('SDK-999' if service == 'outside' else 'SDK-420')
                        + ' private tail', event_id=f'healthy-{index}', service=service,
                        request_status=200, duration_ms=2)]
        source = await section_batch(prepare_preserved_batch(identity, events, WINDOW), LocalPartitionModel())
        compact = await compact_summary_batch(source, Selector(['incident', 'healthy']))
        chunks.extend(compact.chunks)
    before = [immutable(c) for c in chunks]
    scope = replace(cell(chunks[0], 29, 31), source_ids=('outside', 'web', 'worker', 'auth'))
    model, checked, result = await query(chunks, (scope,), 'Inspect SDK-420 across web, worker, and auth services')
    assert 3 <= len(model.calls) <= 6
    assert {call[1]['candidate']['service'] for call in model.calls} == {'web', 'worker', 'auth'}
    assert {item.candidate.chunk.service for item in checked.selected} == {'web', 'worker', 'auth'}
    assert all(call[1]['candidate']['literal_matching_record_indexes'] == [0] for call in model.calls)
    assert all([r['ref'] for r in restore_records(call[1]['candidate']['records'])] == ['e1'] for call in model.calls)
    assert 'incident' not in _json(result) + _json(model.calls)
    assert [immutable(c) for c in chunks] == before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['invalid', 'provider', 'cancel'])
async def test_narrow_late_failure_and_cancellation_publish_no_partial_selection(failure):
    import asyncio

    chunk = await incident_chunk()
    before = immutable(chunk)
    cells = (cell(chunk, 29, 31, 'one'), cell(chunk, 30, 31, 'two'), cell(chunk, 29, 31, 'three'))
    found = await retrieve('What operation was reported?', cells, Backend(chunk), Provider(), preserve_content=True)
    entered, stopped = asyncio.Event(), asyncio.Event()

    class Late(Model):
        async def generate(self, instruction, payload, schema):
            positive = await super().generate(instruction, payload, schema)
            if len(self.calls) == 2:
                if failure == 'provider':
                    raise RuntimeError('PRIVATE_FAILURE_CANARY')
                if failure == 'invalid':
                    return {'label': 'topic_match', 'confidence': True}
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    stopped.set()
            return positive

    model = Late()
    if failure == 'cancel':
        task = asyncio.create_task(remap(found, model, config=CONFIG, preserve_content=True))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stopped.is_set()
    else:
        checked = await remap(found, model, config=CONFIG, preserve_content=True)
        result = assemble_context(checked, preserve_content=True)
        assert not checked.selected and not result['evidence'] and not result['citations']
        assert 'PRIVATE_FAILURE_CANARY' not in _json(result)
        assert 'incident' not in _json(result)
    assert len(model.calls) == 2
    assert 'incident' not in _json(model.calls)
    assert immutable(chunk) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(('question', 'admitted'), [
    ('Compare SDK-421 and SDK-422', True),
    ('Inspect SDK-421 and TRACE-777', False),
    ('Inspect SDK-422 and TRACE-777', True),
    ('Inspect SDK-421 and SDK-999', False),
])
async def test_narrow_literal_field_or_joint_and_and_absent_id(question, admitted):
    _, compact = await fresh([
        event(0, 'EXCLUDED_PHRASE_CANARY code=SDK-421 trace_id=TRACE-777 private tail'),
        event(30, 'healthy code=SDK-421 trace_id=TRACE-888 private tail'),
        event(31, 'healthy code=SDK-422 trace_id=TRACE-777 private tail'),
    ], Selector(['EXCLUDED_PHRASE_CANARY', 'healthy']))
    chunk = compact.chunks[0]
    before = immutable(chunk)
    model, checked, result = await query((chunk,), (cell(chunk, 29, 32),), question)
    assert bool(model.calls) is admitted
    assert bool(checked.selected) is admitted
    if admitted:
        payload = model.calls[0][1]['candidate']
        assert payload['literal_matching_record_indexes'] == ([0, 1] if question.startswith('Compare') else [1])
        assert [r['ref'] for r in restore_records(payload['records'])] == ['e1', 'e2']
    assert 'EXCLUDED_PHRASE_CANARY' not in _json(result) + _json(model.calls)
    assert immutable(chunk) == before


@pytest.mark.asyncio
async def test_optional_answer_highlights_keep_distinct_cell_observations():
    from logchat.rag.answering import answer

    chunk = await incident_chunk()
    before = immutable(chunk)
    _, checked, _ = await query((chunk,), (cell(chunk, 29, 31, 'healthy'), cell(chunk, 31, 32, 'endpoint')))

    class Highlights(Model):
        async def generate(self, instruction, payload, schema):
            self.calls.append((instruction, payload, schema))
            return {'highlights': [{'observation_id': row['id'], 'quote': row['text']}
                                   for row in payload['observations']]}

    model = Highlights()
    result = await answer(checked, model, preserve_content=True)
    assert result['provenance']['answering']['status'] == 'ok'
    observations = model.calls[0][1]['observations']
    assert len({row['id'] for row in observations}) == len(observations) == 4
    facts = {row['cell_id']: row for row in observations if row['kind'] == 'checked_source_observations'}
    assert '"healthy"' in facts['healthy']['text'] and 'excluded-end' not in facts['healthy']['text']
    assert '"excluded-end"' in facts['endpoint']['text'] and '"healthy"' not in facts['endpoint']['text']
    assert all(row['evidence_ids'] == [chunk.chunk_id] for row in result['highlights'])
    assert 'incident' not in _json(result) + _json(model.calls)
    assert immutable(chunk) == before
