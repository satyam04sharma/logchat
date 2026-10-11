"""Active catalog dispatch, exact topic memberships and complete input packing."""
from dataclasses import asdict
import json

import pytest

from tests.catalog_selector import role_selections

from logchat.rag.compact_input import COMPACT_INPUT_VERSION, expand_compact_input
from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, SectionModelError, SummaryConfig, _compact_request,
    compact_summary_batch, records_for_chunk, section_batch,
    validate_compact_chunk,
)
from logchat.rag.source_catalog import build_source_catalog
from logchat.rag.source_observations import _json, _protect_bodies
from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json
from tests.test_compact_v3_activation import Selector
from tests.test_rag_sections import LocalPartitionModel, batch, event


@pytest.mark.parametrize('topic', ['Request failed', 'delayed parcel dispatch',
                                  'deferred crate routing', 'requête échouée', '处理 失败'])
@pytest.mark.parametrize('layout', ['prefix', 'suffix', 'upper'])
def test_opaque_gap_keeps_exact_topic_memberships(topic, layout):
    text = f'{topic} payload_7f93'
    if layout == 'suffix':
        text = f'payload_7f93 {topic}'
    if layout == 'upper':
        text, topic = text.upper(), topic.upper()
    views = {'a': text, 'b': text, 'c': 'routine completion payload_9a12'}
    catalog = build_source_catalog(views, {k: [v] for k, v in views.items()})
    candidates = [p for p in catalog.phrases if topic in p.text]
    assert candidates and all(p.event_keys == ('a', 'b') for p in candidates)
    _protect_bodies([p.text for p in catalog.phrases], list(views.values()))


@pytest.mark.parametrize('words', [('incident', 'healthy'), ('stalled', 'finished'),
                                  ('échoué', 'réussi')])
@pytest.mark.parametrize('reverse', [False, True])
def test_shared_incidental_words_do_not_displace_distinguishing_terms(words, reverse):
    texts = [f'{word} operation private tail' for word in words]
    if reverse:
        texts = [' '.join(reversed(text.split())) for text in texts]
    views = dict(zip(('one', 'two'), texts))
    catalog = build_source_catalog(views, {k: [v] for k, v in views.items()})
    for key, word in zip(views, words):
        assert any(word in p.text and p.event_keys == (key,) for p in catalog.phrases)
    _protect_bodies([p.text for p in catalog.phrases], texts)


@pytest.mark.asyncio
async def test_one_global_catalog_used_before_packing_and_for_every_request(monkeypatch):
    import logchat.rag.source_catalog as catalog_module
    import logchat.rag.sections as sections
    source = await section_batch(batch([event(i, 'Request failed payload_7f93 ' + json.dumps(
        {f'part_{i}_{j}_id': j for j in range(33)})) for i in range(3)]), LocalPartitionModel())
    catalogs, requests = [], []
    original_build, original_request = catalog_module.build_source_catalog, sections._compact_request
    def build(*args, **kwargs):
        catalog = original_build(*args, **kwargs)
        catalogs.append(catalog)
        return catalog
    def request(*args, **kwargs):
        assert len(catalogs) == 1 and kwargs['source_catalog'] is catalogs[0]
        requests.append(kwargs['source_catalog'])
        return original_request(*args, **kwargs)
    monkeypatch.setattr(catalog_module, 'build_source_catalog', build)
    monkeypatch.setattr(sections, '_compact_request', request)
    model = Selector(['Request failed'])
    compact = await compact_summary_batch(source, model)
    assert len(catalogs) == 1 and len(model.calls) == 3 and requests
    assert all(call[0] == CATALOG_COMPACT_INSTRUCTION for call in model.calls)
    expanded_records = []
    for _, payload, _ in model.calls:
        assert payload['input_projection'] == COMPACT_INPUT_VERSION
        rows = expand_compact_input(payload)['records']
        assert len(rows) == 1  # Each 33-field member requires its own request.
        expanded_records.extend(rows)
    # Compare ordered members, not a set: messages must retain their exact
    # timestamp/measurement binding, with refs local to each singleton request.
    assert expanded_records == [
        {'ref': 'e0', **{key: row[key] for key in
                        ('message', 'timestamp', 'request_status', 'duration_ms')}}
        for chunk in source.chunks for row in records_for_chunk(chunk)]
    for chunk in compact.chunks:
        assert 'selector_protocol:source-catalog-selector-v3' in chunk.loss_notes
        assert len(validate_compact_chunk(chunk)['important_fields']) == 33
    serialized = json.dumps(asdict(compact), default=str)
    assert 'phrase_catalog' not in serialized and 'payload_7f93' not in serialized


@pytest.mark.asyncio
async def test_late_unknown_ref_is_atomic_after_feasible_valid_calls():
    source = await section_batch(batch([event(i, 'Request failed payload_7f93 ' + json.dumps(
        {f'part_{i}_{j}_id': j for j in range(33)})) for i in range(3)]), LocalPartitionModel())
    class Late(Selector):
        async def generate(self, *args):
            result = await super().generate(*args)
            if len(self.calls) == 3:
                result['selections'].extend(role_selections(['unknown']))
            return result
    model = Late(['Request failed'])
    with pytest.raises(SectionModelError, match='compact_invalid_output'):
        await compact_summary_batch(source, model)
    assert len(model.calls) == 3 and all(c.compact_evidence is None for c in source.chunks)


@pytest.mark.asyncio
async def test_complete_field_heavy_singleton_and_lossless_table_full_request_cost():
    fields = {f'part_{j}_id': j for j in range(33)}
    fields.update({f'optional_{j}': ('é' * 12 if j % 2 else '\\' * 12) for j in range(20)})
    source = await section_batch(batch([event(0, 'Request failed payload_7f93 ' + json.dumps(fields))]),
                                 LocalPartitionModel())
    model = Selector(['Request failed'], fields=True)
    compact = await compact_summary_batch(source, model)
    assert len(model.calls) == len(compact.chunks) == 1
    instruction, payload, schema = model.calls[0]
    chunk = source.chunks[0]
    rows = records_for_chunk(chunk)
    old, _, _, _ = _compact_request(chunk, rows, SummaryConfig())
    assert all(payload[k] == value for k, value in old.items())
    assert len(payload['field_catalog']) == 53
    assert len(validate_compact_chunk(compact.chunks[0])['important_fields']) == 53
    full = instruction + SYSTEM_SAFETY_SUFFIX + model_json(payload) + model_json(schema)
    objects = {**payload, 'phrase_catalog': [{'phrase_ref': ref, 'text': text}
                                           for ref, text in payload['phrase_catalog']]}
    object_full = instruction + SYSTEM_SAFETY_SUFFIX + model_json(objects) + model_json(schema)
    assert len(full) < len(object_full)
    assert len(full) <= SummaryConfig().input_character_budget
    assert len(full.encode()) <= SummaryConfig().input_byte_budget


@pytest.mark.asyncio
async def test_active_offered_escaping_grammar_still_enforces_render_cap():
    phrases = ['aa' + chr(i) * 110 + 'bb' for i in (1, 2, 3, 4)]
    source = await section_batch(batch([event(i, text + ' payload_7f93')
                                      for i, text in enumerate(phrases)]), LocalPartitionModel())
    model = Selector(phrases)
    with pytest.raises(SectionModelError, match='compact_invalid_output'):
        await compact_summary_batch(source, model)
    assert len(model.calls) == 1
    assert all(any(text == phrase for _, text in model.calls[0][1]['phrase_catalog']) for phrase in phrases)
    assert all(c.compact_evidence is None for c in source.chunks)


@pytest.mark.parametrize('words', [('parcel', 'routing', 'stalled'),
                                  ('stalled', 'parcel', 'routing'),
                                  ('routing', 'stalled', 'parcel')])
def test_single_pattern_natural_language_declared_prefix_tie(words):
    text = ' '.join(words)
    catalog = build_source_catalog({'only': text}, {'only': [text]})
    assert ' '.join(words[:2]) in {p.text for p in catalog.phrases}
    assert words[-1] not in {p.text for p in catalog.phrases}
    _protect_bodies([p.text for p in catalog.phrases], [text])


@pytest.mark.asyncio
async def test_safe_global_catalog_rejects_complementary_coverage_before_generation():
    # Any two tokens reconstruct one body; any one token leaves a view uncovered.
    # This exact three-body conflict must reject before making compact calls.
    views = {'one': 'alpha extra', 'two': 'beta extra', 'nonwitness': 'alpha beta'}
    with pytest.raises(ValueError, match='source_catalog_coverage_conflict'):
        build_source_catalog(views, {key: [text] for key, text in views.items()})
    source = await section_batch(batch([event(i, text) for i, text in enumerate(views.values())]),
                                 LocalPartitionModel())
    model = Selector(['alpha', 'beta'])
    with pytest.raises(SectionModelError, match='compact_catalog_construction_failed'):
        await compact_summary_batch(source, model)
    assert model.calls == [] and all(c.compact_evidence is None for c in source.chunks)


@pytest.mark.parametrize('relation', ['after', 'before', 'without'])
def test_structural_gap_retains_exact_polarity_and_relation_memberships(relation):
    views = {'one': f'not delivered {relation} routing payload_73',
             'two': f'delivered {relation} routing payload_74'}
    catalog = build_source_catalog(views, {key: [text] for key, text in views.items()})
    phrase = f'not delivered {relation} routing'
    assert any(p.text == phrase and p.event_keys == ('one',) for p in catalog.phrases)
    _protect_bodies([p.text for p in catalog.phrases], list(views.values()))
