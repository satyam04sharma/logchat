"""Pure synthetic subdivision controls: no provider/tokenizer/model calls."""
from dataclasses import replace
from datetime import timedelta
import hashlib

import pytest

from logchat.rag.embedding_subdivision import OPERATION_VERSION, record_subset, validate_subdivision
from logchat.rag.scheduler import _encode
from logchat.rag.sections import (
    COMPACT_VERSION, COMPACT_EVIDENCE_V3, _compact_id, _compact_pattern_id, _json,
    _selected_fields, active_compact_context, embedding_text, record_metrics,
    records_for_chunk, validate_compact_batch, validate_compact_chunk,
)
from logchat.rag.source_observations import RENDER_VERSION, render_observations, select_observations
from tests.test_rag_sections import batch, event
from tests.test_compact_embedding_versions import fixture_chunk


def fixture():
    original = batch([event(i) for i in range(6)])
    rows = sorted([r for c in original.chunks for r in records_for_chunk(c)], key=lambda r: r['timestamp'])
    records = [{**{k: v for k, v in row.items() if k not in {'message', 'fingerprint'}},
                'ref': f'e{i}'} for i, row in enumerate(rows)]
    views = {f'e{i}': f'private body {i}: ' + ('operation failed' if i in (0, 2, 4) else 'other')
             + ' discarded suffix' for i in range(6)}
    observations, _ = select_observations({'source_phrases': [
        {'record_ref': 'e4', 'text': 'operation failed'},
        {'record_ref': 'e0', 'text': 'failed'}], 'important_fields': []},
        records, views, {k: [v] for k, v in views.items()}, [], output_budget=8000)
    values = [False, 0, 0.0, -0.0, None, {'λ': [True, 1, 1.0, '雪']}]
    fields = _selected_fields([{'key': 'typed', 'value': value, 'event_refs': [f'e{i}']}
                              for i, value in enumerate(values)], records)
    evidence = {'version': COMPACT_EVIDENCE_V3, 'render_version': RENDER_VERSION,
                'records': records, 'important_fields': fields, 'source_observations': observations}
    parent = replace(original.chunks[0], compression_version=COMPACT_VERSION,
                     summary_model='synthetic', metrics=record_metrics(records),
                     first_event_at=original.window.start, last_event_at=original.window.start + timedelta(seconds=5))
    parent = resign(parent, evidence)
    return replace(original, chunks=(parent,))


def resign(chunk, evidence):
    summary = render_observations(evidence['source_observations'], evidence['records'], evidence['important_fields'])
    chunk = replace(chunk, compact_evidence=_json(evidence), summary=summary,
                    pattern_id=_compact_pattern_id(evidence, summary))
    return replace(chunk, chunk_id=_compact_id(chunk, chunk.compact_evidence, summary, chunk.summary_model))


def split(original):
    parent = original.chunks[0]
    return replace(original, chunks=tuple(record_subset(parent, a, b) for a, b in [(0, 2), (2, 4), (4, 6)]))


def test_exact_typed_conservation_witnesses_and_deterministic_lineage():
    original = fixture()
    final = split(original)
    proof = validate_subdivision(original, final)
    assert proof == validate_subdivision(original, split(original))
    assert proof.operation_version == OPERATION_VERSION
    assert proof.original_payload_sha256 == hashlib.sha256(_encode(original).encode()).hexdigest()
    assert proof.final_payload_sha256 == hashlib.sha256(_encode(final).encode()).hexdigest()
    assert [(x.record_start, x.record_end) for x in proof.leaves] == [(0, 2), (2, 4), (4, 6)]
    fields = []
    for child in final.chunks:
        evidence = validate_compact_chunk(child)
        assert [row['ref'] for row in evidence['records']] == ['e0', 'e1']
        assert [o['record_ref'] for o in evidence['source_observations']] == ['e0', 'e0']
        assert all(o['event_refs'] == ['e0'] for o in evidence['source_observations'])
        fields.extend(evidence['important_fields'])
    assert sorted(_json(f['value']) for f in fields) == sorted(
        _json(f['value']) for f in validate_compact_chunk(original.chunks[0])['important_fields'])
    assert validate_compact_batch(final)
    assert _encode(original) == _encode(fixture())


def test_empty_intersection_only_and_whole_parent_noop():
    original = fixture()
    parent = original.chunks[0]
    assert record_subset(parent, 0, 6) is parent
    final = replace(original, chunks=tuple(record_subset(parent, i, i + 1) for i in range(6)))
    validate_subdivision(original, final)
    for i, child in enumerate(final.chunks):
        assert bool(validate_compact_chunk(child)['source_observations']) == (i % 2 == 0)
    for version in ('compact-evidence-v1', 'compact-evidence-v2'):
        legacy = fixture_chunk(version=version)
        old = replace(batch([event()]), chunks=(legacy,))
        assert record_subset(legacy, 0, 1) is legacy
        assert validate_subdivision(old, old).original_payload_sha256 == validate_subdivision(old, old).final_payload_sha256
        assert embedding_text(legacy) == embedding_text(old.chunks[0])


@pytest.mark.parametrize('mutation', ['quote', 'new_quote', 'field_loss', 'bool_int', 'signed_zero',
    'model', 'notes', 'status', 'duration', 'witness'])
def test_resigned_structurally_valid_mutations_cannot_gain_lineage(mutation):
    original = fixture()
    final = split(original)
    index = 1 if mutation == 'signed_zero' else 0
    child = final.chunks[index]
    evidence = validate_compact_chunk(child)
    if mutation == 'quote':
        evidence['source_observations'][0]['text'] = 'different valid-looking phrase'
    elif mutation == 'new_quote':
        evidence['source_observations'].append({**evidence['source_observations'][0], 'text': 'new'})
    elif mutation == 'field_loss':
        evidence['important_fields'].pop()
    elif mutation == 'bool_int':
        field = next(f for f in evidence['important_fields'] if f['value'] is False)
        field['value'] = 0
        evidence['important_fields'] = _selected_fields(evidence['important_fields'], evidence['records'])
    elif mutation == 'signed_zero':
        next(f for f in evidence['important_fields'] if _json(f['value']) == '-0.0')['value'] = 0.0
        evidence['important_fields'] = _selected_fields(evidence['important_fields'], evidence['records'])
    elif mutation in ('model', 'notes'):
        child = replace(child, **({'summary_model': 'changed'} if mutation == 'model' else {'loss_notes': ('changed',)}))
    elif mutation in ('duration', 'status'):
        evidence['records'][1]['duration_ms' if mutation == 'duration' else 'request_status'] = 42 if mutation == 'duration' else 503
        child = replace(child, metrics=record_metrics(evidence['records']))
    else:
        # Full parent has multiple valid witnesses: changing one is structurally
        # legal but forbidden even when memberships/facts remain identical.
        final = original
        child = original.chunks[0]
        evidence = validate_compact_chunk(child)
        evidence['source_observations'][0]['record_ref'] = 'e0'
    changed = resign(child, evidence)
    validate_compact_chunk(changed)
    chunks = list(final.chunks)
    chunks[index] = changed
    with pytest.raises(ValueError):
        validate_subdivision(original, replace(final, chunks=tuple(chunks)))


@pytest.mark.parametrize('mutation', ['reverse', 'duplicate', 'omit', 'batch_id', 'event_order', 'coverage'])
def test_batch_membership_and_metadata_fail_closed(mutation):
    original = fixture()
    final = split(original)
    if mutation == 'reverse':
        final = replace(final, chunks=tuple(reversed(final.chunks)))
    elif mutation == 'duplicate':
        final = replace(final, chunks=(final.chunks[0], final.chunks[0], final.chunks[2]))
    elif mutation == 'omit':
        final = replace(final, chunks=final.chunks[:-1])
    elif mutation == 'batch_id':
        final = replace(final, batch_id='changed')
    elif mutation == 'event_order':
        final = replace(final, event_keys=tuple(reversed(final.event_keys)))
    else:
        final = replace(final, coverage=tuple(replace(c, reason='changed') for c in final.coverage))
    with pytest.raises(ValueError):
        validate_subdivision(original, final)


def test_child_query_uses_half_open_scope():
    from types import SimpleNamespace
    from logchat.rag.contracts import TimeWindow
    original = fixture()
    child = record_subset(original.chunks[0], 2, 6)
    cell = SimpleNamespace(**{k: getattr(original.identity, k) for k in ('owner_id', 'project_id', 'environment_id')},
        source_ids=(original.identity.source_id,), service=child.service, cell_id='synthetic',
        window=TimeWindow(original.window.start + timedelta(seconds=3), original.window.start + timedelta(seconds=4)))
    context = active_compact_context(child, cell)
    assert len(context['retained_events']) == 1
    assert context['retained_events'][0]['event_id'] == 'customer-event-3'
    assert context['source_observations'] == []


@pytest.mark.parametrize('start,end', [(True, 2), (0, False), (-1, 2), (2, 2), (4, 7), (3, 1)])
def test_invalid_subset_intervals_reject(start, end):
    with pytest.raises(ValueError, match='subdivision_invalid_interval'):
        record_subset(fixture().chunks[0], start, end)


def test_payload_cap_checked_before_lineage_is_issued():
    original = fixture()
    # Loss notes have no per-note cap in SemanticChunk; scheduler's total UTF-8
    # payload cap must still apply, including unchanged parents.
    parent = replace(original.chunks[0], loss_notes=('雪' * 500_001,))
    oversized = replace(original, chunks=(parent,))
    assert len(_encode(oversized).encode()) > 3_000_000
    with pytest.raises(ValueError, match='subdivision_payload_limit'):
        validate_subdivision(oversized, oversized)


def test_no_cross_parent_reassignment_even_for_valid_batch_records():
    original = split(fixture())
    final = replace(original, chunks=tuple(reversed(original.chunks)))
    validate_compact_batch(final)
    with pytest.raises(ValueError, match='subdivision_conservation_mismatch'):
        validate_subdivision(original, final)


def test_child_wrong_metrics_reject_before_lineage():
    original = fixture()
    final = split(original)
    child = final.chunks[0]
    child = replace(child, metrics=replace(child.metrics, duration_sum_ms=99))
    child = replace(child, chunk_id=_compact_id(child, child.compact_evidence, child.summary, child.summary_model))
    with pytest.raises(ValueError, match='invalid_compact_evidence'):
        validate_subdivision(original, replace(final, chunks=(child, *final.chunks[1:])))
