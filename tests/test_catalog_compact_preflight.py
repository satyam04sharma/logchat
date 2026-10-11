"""Offline catalog transport/preflight; production activation is a separate gate."""
from dataclasses import replace
from itertools import combinations

import pytest

from logchat.rag.compact_bounds import compact_reservations
from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, COMPACT_EVIDENCE_V3, RENDER_VERSION,
    SectionModelError, SummaryConfig, _compact_feasible, _compact_request,
    _json, _pack_compact_sections, records_for_chunk, section_batch,
    validate_refinement,
)
from logchat.rag.source_catalog import SELECTOR_PROTOCOL, build_source_catalog, select_catalog_observations
from logchat.rag.source_observations import _protect_bodies, observation_embedding
from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json
from tests.test_rag_sections import LocalPartitionModel, batch, event


async def fixture(messages):
    source = await section_batch(batch([event(i, text) for i, text in enumerate(messages)]), LocalPartitionModel())
    records = [row for chunk in source.chunks for row in records_for_chunk(chunk)]
    views = {row['event_key']: row['message'] for row in records}
    bodies = {key: [text] for key, text in views.items()}
    catalog = build_source_catalog(views, bodies)
    return source, views, {'source_catalog': catalog, 'protected_bodies': bodies}


@pytest.mark.asyncio
async def test_catalog_request_preserves_complete_old_input_and_explicit_new_schema():
    source, views, options = await fixture(['operation completed after delay'] * 35)
    chunk = source.chunks[0]
    records = records_for_chunk(chunk)
    config = SummaryConfig()
    old, old_schema, old_metadata, old_fields = _compact_request(chunk, records, config, views)
    new, schema, metadata, fields = _compact_request(chunk, records, config, views, **options)
    assert all(new[key] == value for key, value in old.items())
    assert metadata == old_metadata and fields == old_fields
    assert new['selector_protocol'] == SELECTOR_PROTOCOL
    assert new['phrase_ref_redundancy'] == 'validate_all_then_exact_first_occurrence'
    assert new['catalog_omissions'] == []
    assert new['phrase_catalog']
    assert len({p[1] for p in new['phrase_catalog']}) == len(new['phrase_catalog'])
    assert set(schema['properties']) == set(schema['required']) == {'phrase_refs', 'important_fields'}
    assert schema['additionalProperties'] is False
    assert schema['properties']['phrase_refs']['maxItems'] == 4
    assert schema['properties']['phrase_refs']['items']['enum'] == [p[0] for p in new['phrase_catalog']]
    assert 'uniqueItems' not in schema['properties']['phrase_refs']
    assert 'source_phrases' in old_schema['properties']
    assert _compact_request(chunk, records, config, views) == (old, old_schema, old_metadata, old_fields)
    _compact_feasible(chunk, records, config, views, **options)
    _protect_bodies([p[1] for p in new['phrase_catalog']], list(views.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['alpha beta', 'alpha beta gamma', 'réconciliation—job terminé avec délai',
                                  'alpha\\"beta\tjob completed after delay', 'atomic'])
async def test_every_small_catalog_selection_and_redundant_response_fits_reservations(text):
    source, views, options = await fixture([text] * 2)
    chunk = source.chunks[0]
    payload, _, records, _ = _compact_request(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    bounds = _compact_feasible(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    catalog = options['source_catalog']
    refs = [p[0] for p in payload['phrase_catalog']]
    local_views = {row['ref']: views[row['event_key']] for row in records}
    bodies = {row['ref']: options['protected_bodies'][row['event_key']] for row in records}
    choices = [list(choice) for n in range(min(4, len(refs)) + 1) for choice in combinations(refs, n)]
    choices.extend([[ref] * 4 for ref in refs])
    for choice in choices:
        response = {'phrase_refs': choice, 'important_fields': []}
        observations, fields = select_catalog_observations(response, records, local_views, bodies, [], catalog,
                                                          output_budget=8000)
        assert fields == []
        evidence = {'version': COMPACT_EVIDENCE_V3, 'render_version': RENDER_VERSION,
                    'records': records, 'important_fields': [], 'source_observations': observations}
        for kind, value in [('selector', response), ('evidence', evidence),
                            ('embedding', observation_embedding(observations, records, []))]:
            assert len(_json(value)) <= bounds[kind + '_chars']
            assert len(_json(value).encode()) <= bounds[kind + '_bytes']
    if text == 'atomic':
        assert not refs
        assert payload['catalog_omissions'] == [
            {'record_ref': row['ref'], 'reason': 'no_safe_proposal_in_grammar'} for row in records]
        assert len(payload['records']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('dimension', ['characters', 'bytes'])
async def test_actual_complete_instruction_payload_schema_boundary(dimension):
    source, views, options = await fixture(['réconciliation—job terminé avec délai'] * 20)
    chunk = source.chunks[0]
    rows = records_for_chunk(chunk)
    payload, schema, _, _ = _compact_request(chunk, rows, SummaryConfig(), views, **options)
    serialized = CATALOG_COMPACT_INSTRUCTION + SYSTEM_SAFETY_SUFFIX + model_json(payload) + model_json(schema)
    if dimension == 'characters':
        config = SummaryConfig(input_character_budget=len(serialized))
        smaller = replace(config, input_character_budget=len(serialized) - 1)
    else:
        config = SummaryConfig(input_byte_budget=len(serialized.encode()))
        smaller = replace(config, input_byte_budget=len(serialized.encode()) - 1)
    _compact_feasible(chunk, rows, config, views, **options)
    with pytest.raises(SectionModelError, match='compact_input_budget_exceeded'):
        _compact_feasible(chunk, rows, smaller, views, **options)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['view', 'body', 'missing_body', 'missing_catalog'])
async def test_preflight_rejects_catalog_source_binding_changes(mutation):
    source, views, options = await fixture(['operation completed after delay'])
    key = next(iter(views))
    if mutation == 'view':
        views[key] += ' altered'
    elif mutation == 'body':
        options['protected_bodies'][key].append('new authenticated body')
    elif mutation == 'missing_body':
        del options['protected_bodies'][key]
    else:
        options['source_catalog'] = None
    with pytest.raises(SectionModelError, match='compact_catalog_invalid_binding'):
        _pack_compact_sections(source, SummaryConfig(), views, **options)


@pytest.mark.asyncio
async def test_global_catalog_rebinds_after_packing_without_record_or_field_loss():
    import json
    texts = ['operation completed after delay ' + json.dumps({f'part_{i}_{j}_id': j for j in range(33)})
             for i in range(3)]
    source, _, options = await fixture(texts)
    # Model projections may be shorter than canonical messages only when supplied
    # by the authenticated caller. Rebuild the sidecar using these explicit views.
    views = {key: 'operation completed after delay' for key in options['protected_bodies']}
    for key, body in views.items():
        options['protected_bodies'][key].append(body)
    options['source_catalog'] = build_source_catalog(views, options['protected_bodies'])
    packed = _pack_compact_sections(source, SummaryConfig(), views, **options)
    assert len(packed.chunks) == 3
    validate_refinement(source, packed)
    original = [r for c in source.chunks for r in records_for_chunk(c)]
    retained = [r for c in packed.chunks for r in records_for_chunk(c)]
    assert retained == original
    catalogs = []
    for chunk in packed.chunks:
        payload, _, _, _ = _compact_request(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
        assert len(payload['required_field_refs']) == 33
        assert len(payload['field_catalog']) == 33
        catalogs.append(payload['phrase_catalog'])
        _compact_feasible(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    assert catalogs[0] == catalogs[1] == catalogs[2]
    _protect_bodies([p[1] for c in catalogs for p in c],
                    [b for bodies in options['protected_bodies'].values() for b in bodies])


@pytest.mark.asyncio
async def test_late_impossible_single_aborts_complete_catalog_preflight():
    import json
    source, views, options = await fixture(['operation completed after delay',
        'operation completed after delay ' + json.dumps({f'part_{i}_id': i for i in range(65)})])
    with pytest.raises(SectionModelError, match='compact_input_budget_exceeded'):
        _pack_compact_sections(source, SummaryConfig(), views, **options)
    assert all(c.compact_evidence is None for c in source.chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['unknown', None, False, 1, '', []])
async def test_unknown_selector_protocol_does_not_receive_legacy_reservations(protocol):
    source, views, options = await fixture(['operation completed after delay'])
    chunk = source.chunks[0]
    payload, _, records, _ = _compact_request(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    payload['selector_protocol'] = protocol
    with pytest.raises(ValueError, match='unknown_compact_selector_protocol'):
        compact_reservations(payload, records, SummaryConfig())


@pytest.mark.asyncio
async def test_full_required_optional_union_bounds_include_typed_utf8_and_escaping():
    import json
    messages = []
    for i in range(2):
        values = {f'correlation_{i}_{j}_id': j for j in range(5)}
        values.update({f'optional_{i}_{j}': ('😀' * 25 if j % 2 else '\\' * 40) for j in range(30)})
        messages.append('typed operation private tail ' + json.dumps(values, ensure_ascii=False))
    source, _, options = await fixture(messages)
    views = {key: 'typed operation private tail' for key in options['protected_bodies']}
    for key, body in views.items():
        options['protected_bodies'][key].append(body)
    options['source_catalog'] = build_source_catalog(views, options['protected_bodies'])
    chunk = source.chunks[0]
    payload, _, records, _ = _compact_request(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    bounds = _compact_feasible(chunk, records_for_chunk(chunk), SummaryConfig(), views, **options)
    required = set(payload['required_field_refs'])
    mandatory = [f for f in payload['field_catalog'] if f['field_ref'] in required]
    optional = [f for f in payload['field_catalog'] if f['field_ref'] not in required]
    assert len(mandatory) == 10 and len(optional) == 60
    phrase = max(payload['phrase_catalog'], key=lambda p: len(p[0]))[0]
    for byte in (False, True):
        def cost(field):
            text = _json(field)
            return len(text.encode()) if byte else len(text)
        selected = mandatory + sorted(optional, key=cost, reverse=True)[:54]
        field_refs = [f['field_ref'] for f in selected]
        fields = [{k: f[k] for k in ('key', 'value', 'event_refs')} for f in selected]
        response = {'phrase_refs': [phrase] * 4, 'important_fields': field_refs}
        observations, returned = select_catalog_observations(response, records,
            {row['ref']: views[row['event_key']] for row in records},
            {row['ref']: options['protected_bodies'][row['event_key']] for row in records},
            field_refs, options['source_catalog'], output_budget=8000)
        assert returned == field_refs
        evidence = {'version': COMPACT_EVIDENCE_V3, 'render_version': RENDER_VERSION,
                    'records': records, 'important_fields': fields, 'source_observations': observations}
        for kind, value in [('selector', response), ('evidence', evidence),
                            ('embedding', observation_embedding(observations, records, fields))]:
            assert len(_json(value)) <= bounds[kind + '_chars']
            assert len(_json(value).encode()) <= bounds[kind + '_bytes']


@pytest.mark.asyncio
@pytest.mark.parametrize('word', ['operation', 'réconciliation'])
async def test_oversized_single_catalog_is_rejected_not_trimmed(word):
    source, views, options = await fixture([' '.join(f'{word}{i}' for i in range(100))])
    catalog = options['source_catalog']
    assert len(catalog.phrases) > 100
    before = catalog.phrases
    with pytest.raises(SectionModelError, match='compact_input_budget_exceeded'):
        _pack_compact_sections(source, SummaryConfig(), views, **options)
    assert catalog.phrases == before
