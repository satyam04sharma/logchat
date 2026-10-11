"""Pure v2 codec controls; no models, counters, tokenizers, or native assets."""
from copy import deepcopy
import json

import pytest

from logchat.rag.compact_input import (
    COLUMNAR_INPUT_VERSION, COMPACT_INPUT_VERSION, expand_compact_input,
    project_compact_input, validate_compact_input_projection,
)
from tests.test_compact_input_codec import complete_input


def project(original):
    return project_compact_input(original, version=COLUMNAR_INPUT_VERSION)


def spelling(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)


def cell(payload, table, row, column, value):
    data = payload[table]
    data['rows'][row][data['columns'].index(column)] = value


@pytest.mark.parametrize('heterogeneous', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_complete_typed_roundtrip_and_presence(heterogeneous, empty):
    original = complete_input()
    if heterogeneous:
        original['records'][0]['optional'] = None
        original['records'][1]['optional'] = False
        original['field_catalog'][0]['optional'] = {'雪': ['\\', '\n', -0.0]}
    if empty:
        original['records'] = []
        original['field_catalog'] = []
    before = deepcopy(original)
    encoded = project(original)
    saved = json.loads(json.dumps(encoded))
    validate_compact_input_projection(original, saved)
    assert spelling(expand_compact_input(saved)) == spelling(original)
    assert project(expand_compact_input(saved)) == encoded
    for name in ('records', 'field_catalog'):
        assert ('presence' in encoded[name]) == (heterogeneous and not empty)
    expanded = expand_compact_input(encoded)
    expanded['context']['service'] = 'changed'
    assert original == before
    assert encoded == saved
    if not empty and not heterogeneous:
        assert len(spelling(encoded).encode()) < len(spelling(project_compact_input(original)).encode())


def test_header_order_deterministic_independent_of_object_key_order():
    original = complete_input()
    shuffled = deepcopy(original)
    for name in ('records', 'field_catalog'):
        shuffled[name] = [dict(reversed(list(row.items()))) for row in shuffled[name]]
    assert project(original) == project(shuffled)
    assert expand_compact_input(project(original))['records'] == original['records']


@pytest.mark.parametrize('table', ['records', 'field_catalog'])
@pytest.mark.parametrize('mutate', [
    lambda t: t.update(extra=[]),
    lambda t: t.pop('columns'),
    lambda t: t.update(columns={}),
    lambda t: t['columns'].append(t['columns'][0]),
    lambda t: t['columns'].__setitem__(0, False),
    lambda t: t['columns'].reverse(),
    lambda t: t['columns'].append('unused'),
    lambda t: t['rows'][0].append(None),
    lambda t: t['rows'][0].pop(),
    lambda t: t['rows'].__setitem__(0, {}),
    lambda t: t.update(rows={}),
    lambda t: t.update(presence=None),
    lambda t: t.update(presence=[]),
    lambda t: t.update(presence=[[True] * len(t['columns']) for _ in t['rows']]),
    lambda t: t.update(presence=[[1] * len(t['columns']) for _ in t['rows']]),
    lambda t: t.update(presence=[[False] * len(t['columns']) for _ in t['rows']]),
])
def test_invalid_table_envelopes_reject(table, mutate):
    encoded = project(complete_input())
    mutate(encoded[table])
    with pytest.raises(ValueError, match='^compact_input_projection_invalid$'):
        expand_compact_input(encoded)


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(input_projection='unknown'),
    lambda p: p.pop('input_projection'),
    lambda p: p['message_dictionary'].update(m2='unused'),
    lambda p: p['message_dictionary'].update(m2=p['message_dictionary']['m0']),
    lambda p: p['message_dictionary'].update(m0=False),
    lambda p: cell(p, 'records', 0, 'message_ref', 'missing'),
    lambda p: cell(p, 'records', 0, 'message_ref', 0),
    lambda p: cell(p, 'records', 0, 'ref', False),
    lambda p: cell(p, 'records', 0, 'ref', 'e1'),
])
def test_invalid_version_references_and_dictionary_reject(mutate):
    encoded = project(complete_input())
    mutate(encoded)
    with pytest.raises(ValueError, match='^compact_input_projection_invalid$'):
        expand_compact_input(encoded)


@pytest.mark.parametrize('mutate', [
    lambda p: cell(p, 'records', 3, 'message_ref', 'm1'),
    lambda p: cell(p, 'records', 0, 'duration_ms', False),
    lambda p: cell(p, 'records', 1, 'duration_ms', 0.0),
    lambda p: cell(p, 'records', 2, 'duration_ms', 1),
    lambda p: cell(p, 'field_catalog', 1, 'value', 0),
    lambda p: cell(p, 'field_catalog', 2, 'value', 0.0),
    lambda p: cell(p, 'field_catalog', 0, 'event_refs', ['e2', 'e1']),
    lambda p: p['records']['rows'].reverse(),
    lambda p: p['field_catalog']['rows'].pop(),
    lambda p: p['phrase_catalog'].pop(),
    lambda p: p['required_field_refs'].clear(),
    lambda p: p.update(extra='trailing payload'),
])
def test_complete_proof_rejects_structurally_valid_semantic_corruption(mutate):
    original = complete_input()
    encoded = project(original)
    mutate(encoded)
    with pytest.raises(ValueError, match='compact_input_projection_(invalid|mismatch)'):
        validate_compact_input_projection(original, encoded)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), (1, 2), {1: 'coercion'}])
def test_invalid_json_rejects_in_both_directions(value):
    original = complete_input()
    original['records'][0]['extra'] = value
    with pytest.raises(ValueError, match='projection_invalid'):
        project(original)
    encoded = project(complete_input())
    cell(encoded, 'field_catalog', 0, 'value', value)
    with pytest.raises(ValueError, match='projection_invalid'):
        expand_compact_input(encoded)


def test_empty_or_absent_catalog_and_optional_null_remain_distinct():
    original = {'records': [{'ref': 'e0', 'message': '雪'},
                            {'ref': 'e1', 'message': '雪', 'optional': None}]}
    assert 'field_catalog' not in project(original)
    assert spelling(expand_compact_input(project(original))) == spelling(original)
    encoded = project(original)
    optional = encoded['records']['columns'].index('optional')
    encoded['records']['presence'][0][optional] = True
    with pytest.raises(ValueError, match='projection_(invalid|mismatch)'):
        validate_compact_input_projection(original, encoded)


def test_historical_v1_encoding_and_expansion_stay_unchanged():
    original = {'records': [{'ref': 'e0', 'message': '雪'}, {'ref': 'e1', 'message': '雪'}]}
    historical = {'records': [{'ref': 'e0', 'message_ref': 'm0'},
                              {'ref': 'e1', 'message_ref': 'm0'}],
                  'input_projection': 'compact-message-dictionary-v1',
                  'message_dictionary': {'m0': '雪'}}
    assert COMPACT_INPUT_VERSION == 'compact-message-dictionary-v1'
    assert project_compact_input(original) == historical
    assert expand_compact_input(historical) == original
    validate_compact_input_projection(original, historical)


@pytest.mark.asyncio
@pytest.mark.parametrize('catalog', [False, True])
@pytest.mark.parametrize('version', [COMPACT_INPUT_VERSION, COLUMNAR_INPUT_VERSION])
async def test_both_versions_preserve_real_payload_choices_and_exact_bounds(catalog, version):
    from logchat.rag.compact_bounds import compact_reservations
    from logchat.rag.sections import SummaryConfig, _expanded_compact_request, records_for_chunk
    from tests.test_catalog_compact_preflight import fixture

    source, views, options = await fixture(['operation failed with rare detail'] * 4)
    chunk = source.chunks[0]
    config = SummaryConfig()
    original, schema, metadata, fields = _expanded_compact_request(
        chunk, records_for_chunk(chunk), config, views, **(options if catalog else {}))
    before = deepcopy((original, schema, metadata, fields))
    encoded = project_compact_input(original, version=version)
    validate_compact_input_projection(original, encoded)
    assert spelling(expand_compact_input(encoded)) == spelling(original)
    assert compact_reservations(encoded, metadata, config) == compact_reservations(original, metadata, config)
    assert (original, schema, metadata, fields) == before
    encoded['records'] = {}
    with pytest.raises(ValueError, match='projection_invalid'):
        compact_reservations(encoded, metadata, config)


def test_columnar_alias_relabelling_and_first_use_order_reject():
    encoded = project(complete_input())
    encoded['message_dictionary']['alias'] = encoded['message_dictionary'].pop('m0')
    table = encoded['records']
    index = table['columns'].index('message_ref')
    for row in table['rows']:
        if row[index] == 'm0':
            row[index] = 'alias'
    with pytest.raises(ValueError, match='projection_invalid'):
        expand_compact_input(encoded)
    encoded = project(complete_input())
    encoded['records']['rows'].insert(0, encoded['records']['rows'].pop(2))
    with pytest.raises(ValueError, match='projection_invalid'):
        expand_compact_input(encoded)
