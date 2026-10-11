"""Pure catalog v2 field contract; no provider or native token execution."""
from copy import deepcopy
import json

import pytest

from logchat.rag.compact_bounds import compact_reservations
from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, SummaryConfig, SectionModelError, _compact_request,
    compact_summary_batch, records_for_chunk, validate_compact_chunk,
)
from logchat.rag.source_catalog import SELECTOR_PROTOCOL, select_catalog_observations
from logchat.rag.source_observations import _json, select_observations
from pipeline.models import _validate_schema
from pipeline.model_transport import generation_body, model_json
from pipeline.token_budget import request_digest
from tests.test_catalog_compact_preflight import fixture
from tests.test_compact_v3_activation import Selector


async def request(count=33, required=0):
    values = {(f'part_{i}_id' if i < required else f'optional_{i}'): i for i in range(count)}
    items = list(values.items())
    messages = ['operation completed after delay ' + json.dumps(dict(items[i:i + 33]))
                for i in range(0, max(1, count), 33)]
    source, views, options = await fixture(messages)
    chunk = source.chunks[0]
    args = (chunk, records_for_chunk(chunk), SummaryConfig(), views)
    payload, schema, records, _ = _compact_request(*args, **options)
    def select(value):
        return select_catalog_observations(value, records,
            {row['ref']: views[row['event_key']] for row in records},
            {row['ref']: options['protected_bodies'][row['event_key']] for row in records},
            [f['field_ref'] for f in payload['field_catalog']], options['source_catalog'], output_budget=8000)
    return source, args, options, payload, schema, records, select


@pytest.mark.asyncio
async def test_actual_shape_all_seven_choices_order_and_memberships_survive():
    _, args, options, payload, schema, records, select = await request()
    known = [f['field_ref'] for f in payload['field_catalog']]
    chosen = [known[i] for i in (6, 2, 5, 0, 4, 1, 3)]
    original = {'phrase_refs': [payload['phrase_catalog'][0][0]] * 4,
                'important_fields': (chosen * 5)[:33]}
    before = deepcopy(original)
    _validate_schema(original, schema)
    observations, fields = select(original)
    assert fields == chosen and len(fields) == 7 and original == before
    assert observations == select({**original, 'phrase_refs': original['phrase_refs'][:1],
                                   'important_fields': chosen})[0]
    assert observations[0]['event_refs'] == ['e0']
    legacy_payload, legacy_schema, _, _ = _compact_request(*args)
    assert payload['field_catalog'] == legacy_payload['field_catalog']
    assert schema['properties']['important_fields']['items'] == legacy_schema['properties']['important_fields']['items']
    assert legacy_schema['properties']['important_fields']['uniqueItems'] is True
    with pytest.raises(ValueError, match='duplicate'):
        _validate_schema({'source_phrases': [], 'important_fields': original['important_fields']}, legacy_schema)
    with pytest.raises(ValueError, match='invalid_source_observation_fields'):
        select_observations({'source_phrases': [], 'important_fields': chosen * 2}, records,
            {'e0': args[3][records[0]['event_key']]}, {'e0': []}, known, output_budget=8000)
    repeated = _compact_request(*args, **options)
    assert repeated[:2] == (payload, schema)
    body = generation_body('pure-mock', CATALOG_COMPACT_INSTRUCTION, payload, schema)
    assert model_json(body) == model_json(generation_body('pure-mock', CATALOG_COMPACT_INSTRUCTION, *repeated[:2]))
    assert payload['selector_protocol'] == SELECTOR_PROTOCOL == 'source-catalog-selector-v2'
    assert body['format'] == schema
    assert request_digest(body) == request_digest(generation_body(
        'pure-mock', CATALOG_COMPACT_INSTRUCTION, *repeated[:2]))
    assert request_digest(body) != request_digest(generation_body(
        'pure-mock', CATALOG_COMPACT_INSTRUCTION, legacy_payload, legacy_schema))


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 33])
async def test_incoming_64_and_original_output_reservations(count):
    _, _, _, payload, schema, records, select = await request(count)
    ref = max((f['field_ref'] for f in payload['field_catalog']), key=len)
    response = {'phrase_refs': [payload['phrase_catalog'][0][0]] * 4, 'important_fields': [ref] * 64}
    _validate_schema(response, schema)
    assert select(response)[1] == [ref]
    bounds = compact_reservations(payload, records, SummaryConfig())
    assert len(_json(response)) <= bounds['selector_chars']
    assert len(_json(response).encode()) <= bounds['selector_bytes']
    historical = compact_reservations({**payload, 'selector_protocol': 'source-catalog-selector-v1'}, records, SummaryConfig())
    assert historical['selector_chars'] < bounds['selector_chars']
    assert all(historical[k] == v for k, v in bounds.items() if not k.startswith('selector_'))
    with pytest.raises(ValueError, match='invalid_source_catalog_output'):
        select_catalog_observations(response, records, {}, {}, [], None, output_budget=len(_json(response)) - 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('tail', ['unknown', 'F0', False, 1, None, [], {}])
async def test_every_trailing_original_entry_rejects(tail):
    _, _, _, payload, schema, _, select = await request(1)
    value = {'phrase_refs': [], 'important_fields': [payload['field_catalog'][0]['field_ref']] * 63 + [tail]}
    with pytest.raises(ValueError):
        _validate_schema(value, schema)
    with pytest.raises(ValueError, match='invalid_source_observation_fields'):
        select(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['65', 'extra', 'not_list', 'phrase5', 'phrase_unknown'])
async def test_original_caps_and_shape_reject_before_dedup(mutation):
    _, _, _, payload, schema, _, select = await request(1)
    value = {'phrase_refs': [], 'important_fields': [payload['field_catalog'][0]['field_ref']] * 64}
    if mutation == '65':
        value['important_fields'].append(value['important_fields'][0])
    elif mutation == 'extra':
        value['extra'] = []
    elif mutation == 'not_list':
        value['important_fields'] = 'f0'
    else:
        value['phrase_refs'] = [payload['phrase_catalog'][0][0]] * (5 if mutation == 'phrase5' else 3)
        if mutation == 'phrase_unknown':
            value['phrase_refs'].append('unknown')
    with pytest.raises(ValueError):
        _validate_schema(value, schema)
    with pytest.raises(ValueError):
        select(value)


@pytest.mark.asyncio
async def test_empty_field_catalog_accepts_only_empty_fields():
    _, _, _, payload, schema, _, select = await request(0)
    assert payload['field_catalog'] == []
    assert schema['properties']['important_fields']['maxItems'] == 0
    value = {'phrase_refs': [], 'important_fields': []}
    _validate_schema(value, schema)
    assert select(value) == ([], [])
    for fields in (['f0'], [False], ['f0'] * 64):
        with pytest.raises(ValueError):
            _validate_schema({**value, 'important_fields': fields}, schema)
        with pytest.raises(ValueError):
            select({**value, 'important_fields': fields})


@pytest.mark.asyncio
@pytest.mark.parametrize('union_count', [33, 64, 65])
async def test_active_response_uses_sent_schema_and_required_union_is_not_trimmed(union_count):
    required = 33 if union_count == 33 else 1
    source, _, _, _, _, _, _ = await request(union_count, required)
    class Fields(Selector):
        async def generate(self, instruction, payload, schema):
            self.calls.append((instruction, deepcopy(payload), deepcopy(schema)))
            refs = [f['field_ref'] for f in payload['field_catalog']]
            if union_count == 33:
                selected = (refs[:7] * 5)[:33]
            else:
                selected = [r for r in refs if r not in payload['required_field_refs']]
                selected += selected[:64 - len(selected)]
            value = {'phrase_refs': [], 'important_fields': selected}
            return _validate_schema(value, schema)
    model = Fields([])
    if union_count == 65:
        with pytest.raises(SectionModelError, match='compact_output_budget_exceeded'):
            await compact_summary_batch(source, model)
        assert all(c.compact_evidence is None for c in source.chunks)
    else:
        compact = await compact_summary_batch(source, model)
        fields = validate_compact_chunk(compact.chunks[0])['important_fields']
        offered = model.calls[0][1]['field_catalog']
        assert fields == [{k: f[k] for k in ('key', 'value', 'event_refs')} for f in sorted(offered, key=lambda f: (f['key'], _json(f['value'])))]
    assert len(model.calls) == 1
