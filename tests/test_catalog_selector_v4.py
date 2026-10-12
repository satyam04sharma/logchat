"""Pure v4 catalog selector controls: plain phrase refs, presentation order, bounds."""
from logchat.rag.compact_input import expand_compact_input
from copy import deepcopy
from dataclasses import replace
import json

import jsonschema
import pytest

from logchat.rag.compact_bounds import compact_reservations
from logchat.rag.sections import SummaryConfig, _compact_request, records_for_chunk
from logchat.rag.source_catalog import select_catalog_observations
from logchat.rag.source_observations import _json, _protect_bodies, observation_embedding, select_observations
from pipeline.models import _validate_schema
from tests.test_catalog_compact_preflight import fixture


async def contract(messages=None):
    source, views, options = await fixture(messages or (
        ['routine directory listing served successfully'] * 32
        + ['rare reconciliation job exhausted retry budget after delay']))
    chunk = source.chunks[0]
    payload, schema, records, _ = _compact_request(
        chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    local_views = {r['ref']: views[r['event_key']] for r in records}
    bodies = {r['ref']: options['protected_bodies'][r['event_key']] for r in records}
    catalog = options['source_catalog']
    def select(value):
        return select_catalog_observations(value, records, local_views, bodies,
            [f['field_ref'] for f in expand_compact_input(payload)['field_catalog']], catalog, output_budget=8000)
    return payload, schema, records, local_views, bodies, catalog, select


def response(*selections):
    return {'selections': list(selections), 'important_fields': []}


def choice(ref):
    return ref


@pytest.mark.asyncio
@pytest.mark.parametrize('combined', [False, True])
async def test_model_chosen_combined_and_complementary_rare_quotes(combined):
    payload, schema, records, views, bodies, catalog, select = await contract()
    offered = dict((text, ref) for ref, text in payload['phrase_catalog'])
    texts = (['reconciliation job exhausted retry budget'] if combined else
             ['reconciliation job', 'exhausted retry budget'])
    selected = [choice(offered[text]) for text in texts]
    value = response(*selected)
    original = deepcopy(value)
    jsonschema.Draft7Validator(schema).validate(value)
    _validate_schema(value, schema)
    observations, fields = select(value)
    assert value == original and fields == []
    assert [o['text'] for o in observations] == texts
    assert all(o['event_refs'] == ['e32'] and o['exact_metrics']['event_count'] == 1 for o in observations)
    legacy = {'source_phrases': [{'record_ref': 'e32', 'text': text} for text in texts], 'important_fields': []}
    assert (observations, fields) == select_observations(legacy, records, views, bodies, [], output_budget=8000)
    durable = _json(observation_embedding(observations, records, fields)) + _json(observations)
    assert 'roles' not in durable and 'phrase_ref' not in durable
    # The rare record's complete phrases are presented before repeated templates.
    first = payload['phrase_catalog'][0][1]
    assert first in 'rare reconciliation job exhausted retry budget after delay' and ' ' in first
    _protect_bodies([o['text'] for o in observations], [*views.values(), *(b for bs in bodies.values() for b in bs)])


@pytest.mark.asyncio
async def test_every_original_choice_and_membership_is_selectable_and_empty_is_valid():
    payload, schema, records, views, bodies, catalog, select = await contract()
    before = deepcopy((records, views, bodies, catalog))
    bound = catalog.bind(records, views, bodies)
    # Presentation reorders but never adds, drops or alters a bound choice.
    assert sorted(payload['phrase_catalog']) == sorted([p['phrase_ref'], p['text']] for p in bound)
    assert len(payload['phrase_catalog']) == len(bound)
    assert schema['properties']['selections']['items']['enum'] == [p[0] for p in payload['phrase_catalog']]
    for item in bound:
        value = response(choice(item['phrase_ref']))
        jsonschema.Draft7Validator(schema).validate(value)
        observations, _ = select(value)
        assert observations[0]['event_refs'] == item['event_refs']
        assert observations[0]['text'] == item['text']
    jsonschema.Draft7Validator(schema).validate(response())
    assert select(response()) == ([], [])
    assert (records, views, bodies, catalog) == before
    _protect_bodies([p['text'] for p in bound], [*views.values(), *(b for bs in bodies.values() for b in bs)])


BAD_CHOICES = [None, False, 0, [], {}, {'phrase_ref': 'p0'}, {'phrase_ref': 'p0', 'roles': ['context']},
    'unknown', 'P0', 'p0 ', ['p0']]


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', BAD_CHOICES)
async def test_every_malformed_original_choice_rejects_even_after_three_known_duplicates(bad):
    _, schema, _, _, _, _, select = await contract(['operation completed after delay'])
    valid = choice('p0')
    for value in (response(bad), response(valid, valid, valid, bad)):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft7Validator(schema).validate(value)
        with pytest.raises(ValueError):
            select(value)


@pytest.mark.asyncio
async def test_exact_four_dedup_first_occurrence_only_after_complete_validation():
    payload, schema, _, _, _, _, select = await contract(['operation completed after delay'])
    a, b = [p[0] for p in payload['phrase_catalog'][:2]]
    value = response(b, a, b, a)
    original = deepcopy(value)
    jsonschema.Draft7Validator(schema).validate(value)
    assert select(value) == select(response(*value['selections'][:2]))
    assert value == original
    for bad in (response(*value['selections'], a),
                {'phrase_refs': [a], 'important_fields': []},
                {'source_phrases': [], 'important_fields': []},
                {**value, 'phrase_refs': []}, {'selections': []},
                {**value, 'selections': None}, {**value, 'selections': {}},
                {**value, 'selections': 'p0'}, {**value, 'extra': []}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft7Validator(schema).validate(bad)
        with pytest.raises(ValueError):
            select(bad)


@pytest.mark.asyncio
async def test_complete_serialized_ref_envelopes_and_historical_reservations():
    payload, schema, records, views, bodies, catalog, _ = await contract(['alpha\\"beta réconciliation 😀 after delay'])
    # Synthetic refs stress serializer escaping and UTF-8 without changing text,
    # membership or catalog eligibility. Production refs are shorter ASCII IDs.
    escaped_ref = 'p\\"\n😀é'
    catalog = replace(catalog, phrases=(replace(catalog.phrases[0], phrase_ref=escaped_ref),))
    payload = {**expand_compact_input(payload), 'phrase_catalog': [[escaped_ref, catalog.phrases[0].text]],
               'field_catalog': [{'field_ref': 'f\\"😀', 'key': 'key', 'value': '😀\\"', 'event_refs': ['e0']}],
               'required_field_refs': []}
    bounds = compact_reservations(payload, records, SummaryConfig())
    sizes = []
    for count in range(5):
            value = {'selections': [choice(escaped_ref)] * count, 'important_fields': ['f\\"😀'] * 64}
            observations, fields = select_catalog_observations(value, records, views, bodies,
                ['f\\"😀'], catalog, output_budget=8000)
            assert fields == ['f\\"😀'] and len(observations) == bool(count)
            sizes.append((len(_json(value)), len(_json(value).encode())))
    assert max(s[0] for s in sizes) == bounds['selector_chars']
    assert max(s[1] for s in sizes) == bounds['selector_bytes']
    assert bounds['selector_bytes'] > bounds['selector_chars']
    for version, field_count in [('v1', 1), ('v2', 64)]:
        historical = compact_reservations({**payload, 'selector_protocol': 'source-catalog-selector-' + version}, records, SummaryConfig())
        value = {'phrase_refs': [escaped_ref] * 4, 'important_fields': ['f\\"😀'] * field_count}
        assert historical['selector_chars'] == len(_json(value))
        assert historical['selector_bytes'] == len(_json(value).encode())
        assert all(historical[k] == v for k, v in bounds.items() if not k.startswith('selector_'))


@pytest.mark.asyncio
async def test_v4_durable_chunks_match_and_historical_notes_read():
    from dataclasses import asdict
    from logchat.rag.sections import compact_summary_batch, embedding_text, validate_compact_chunk
    from tests.test_compact_v3_activation import Selector

    messages = ['routine directory listing served successfully'] * 32 + [
        'rare reconciliation job exhausted retry budget after delay']
    source, _, _ = await fixture(messages)
    first = await compact_summary_batch(source, Selector(['reconciliation job', 'exhausted retry budget']))
    chunk = first.chunks[0]
    evidence = validate_compact_chunk(chunk)
    assert evidence['version'] == 'compact-evidence-v3'
    assert 'selector_protocol:source-catalog-selector-v4' in chunk.loss_notes
    serialized = json.dumps(asdict(first), default=str) + embedding_text(chunk)
    assert '"roles"' not in serialized
    for version in ('v1', 'v2', 'v3'):
        historical = replace(chunk, loss_notes=tuple(
            note.replace('source-catalog-selector-v4', 'source-catalog-selector-' + version)
            for note in chunk.loss_notes))
        assert validate_compact_chunk(historical) == evidence
        assert embedding_text(historical) == embedding_text(chunk)


def test_presentation_order_is_a_rare_template_first_permutation():
    from logchat.rag.sections import _presentation_order
    views = {'e0': 'Directory index forbidden by rule', 'e1': 'Directory index forbidden by rule',
             'e2': 'Directory index forbidden by rule', 'e3': 'payment gateway connection timed out\nStructured'}
    common = ['e0', 'e1', 'e2']
    bound = [{'phrase_ref': 'p0', 'text': 'Directory', 'event_refs': common},
             {'phrase_ref': 'p1', 'text': 'Directory index forbidden', 'event_refs': common},
             {'phrase_ref': 'p2', 'text': 'timed out', 'event_refs': ['e3']},
             {'phrase_ref': 'p3', 'text': 'out\nStructured', 'event_refs': ['e3']},
             {'phrase_ref': 'p4', 'text': 'payment gateway connection timed out', 'event_refs': ['e3']},
             {'phrase_ref': 'p5', 'text': 'by rule', 'event_refs': common}]
    ordered = [p['phrase_ref'] for p in _presentation_order(bound, views)]
    # Rare template lead, then common leads; contained or multi-line fragments last.
    assert ordered == ['p4', 'p1', 'p5', 'p0', 'p2', 'p3']
    assert _presentation_order([], {}) == []


def test_records_differing_only_by_ids_share_a_template_and_lead_with_shared_operation():
    from logchat.rag.sections import _presentation_order
    views = {f'e{i}': f'Session renewal refused for request PROD-{i}; LIB_AUTH_PROD_409' for i in range(4)}
    bound = [{'phrase_ref': f'p{i}', 'text': f'refused for request PROD-{i}; LIB_AUTH_PROD_409',
              'event_refs': [f'e{i}']} for i in range(4)]
    bound.append({'phrase_ref': 'p9', 'text': 'Session renewal refused for request PROD',
                  'event_refs': list(views)})
    ordered = [p['text'] for p in _presentation_order(bound, views)]
    assert ordered[0] == 'Session renewal refused for request PROD'
    assert sorted(ordered) == sorted(p['text'] for p in bound)
