"""Production typed-field regression: exact structured quotes, no native model."""
from copy import deepcopy
from itertools import combinations
import json

import pytest

from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, SummaryConfig, _compact_request, _source_fields,
    compact_summary_batch, records_for_chunk, validate_compact_chunk,
    validate_refinement,
)
from logchat.rag.source_catalog import build_source_catalog, _structured_spans
from logchat.rag.source_observations import _json, _protect_bodies
from tests.catalog_selector import select_topics
from tests.test_compact_message_projection import fixture, catalog_request_options
from tests.test_grouping_projection import sidecar


CASES = [
    ({'detail': 'parcel routing stalled', 'code': 731}, 'parcel routing stalled'),
    ({'renamed': 731, 'elsewhere': 'parcel routing stalled'}, 'parcel routing stalled'),
    ({'outer': {'label': 'parcel routing stalled', 'code': 731}}, 'parcel routing stalled'),
    ({'detail': 'réconciliation terminée', 'code': 731}, 'réconciliation terminée'),
    ({'detail': '处理 失败', 'code': 731}, '处理 失败'),
    ({'label': 'parcel routing stalled', 'number': -31.25, 'flag': True,
      'absent': None, 'items': ['parcel routing stalled', False, 731]}, 'parcel routing stalled'),
    ({'list': [{'label': 'parcel routing stalled'}, 731, None]}, 'parcel routing stalled'),
    ({'flag': True, 'code': 731}, 'true'),
    ({'absent': None, 'code': 731}, 'null'),
    ({'fraction': -31.25, 'code': 731}, '31.25'),
    # Key/value collision: reserving every key would lose this topic.
    ({'parcel': 'parcel routing stalled', 'unrelated': 731}, 'parcel routing stalled'),
    ({'detail': 'quote "inside" escaped \\ path', 'code': 731}, 'inside'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['', 'notice '])
@pytest.mark.parametrize('data,topic', CASES)
async def test_actual_typed_values_offer_exact_content_and_preserve_complete_input(
        monkeypatch, prefix, data, topic):
    body = prefix + json.dumps(data, ensure_ascii=False)
    events, source = await fixture([dict(message=body, event_id='structured-1',
        timestamp='2026-10-10T00:00:00Z', duration_ms=7.5, request_status=503)])
    before = deepcopy(source)
    production_calls = []
    def capture(views, bodies, *, typed_values):
        rows = [row for chunk in source.chunks for row in records_for_chunk(chunk)]
        expected = {_json(v) for row in rows for v in _source_fields(row).values()
                    if not isinstance(v, dict)}
        assert typed_values == expected
        production_calls.append(1)
        return build_source_catalog(views, bodies, typed_values=typed_values)
    monkeypatch.setattr('logchat.rag.source_catalog.build_source_catalog', capture)
    candidates = sidecar(events)
    messages, options = catalog_request_options(source, candidates)
    catalog = options['source_catalog']
    texts = {p.text for p in catalog.phrases}
    assert topic in texts and not catalog.omissions
    bodies = [*messages.values(), *(b for values in options['protected_bodies'].values() for b in values)]
    _protect_bodies(texts, bodies)
    # Exhaustive accepted-size subsets for the small two-field regression.
    if len(texts) <= 12:
        for size in range(min(4, len(texts)) + 1):
            for subset in combinations(texts, size):
                _protect_bodies(subset, bodies)
    for phrase in catalog.phrases:
        assert phrase.event_keys == tuple(key for key, view in messages.items() if phrase.text in view)
        assert phrase.text in body  # No decoded escape or inferred offset.

    class ExactModel:
        local_only = preserve_content = True
        chat_model = 'structured-mock'
        calls = 0

        async def generate(self, instruction, payload, schema):
            self.calls += 1
            assert instruction == CATALOG_COMPACT_INSTRUCTION
            assert set(schema['properties']) == {'phrase_refs', 'important_fields'}
            rows = records_for_chunk(source.chunks[0])
            expected, expected_schema, _, _ = _compact_request(
                source.chunks[0], rows, SummaryConfig(), messages, **options)
            assert payload == expected and schema == expected_schema
            legacy, _, _, _ = _compact_request(source.chunks[0], rows, SummaryConfig(), messages)
            assert all(payload[key] == value for key, value in legacy.items())
            return select_topics(payload, [topic], fields=True)

    model = ExactModel()
    compact = await compact_summary_batch(source, model, model_events=candidates)
    assert model.calls == 1 and source == before and production_calls == [1]
    validate_refinement(source, compact)
    evidence = validate_compact_chunk(compact.chunks[0])
    observation, = evidence['source_observations']
    assert observation['text'] == topic
    assert observation['event_refs'] == ['e0']
    assert observation['exact_metrics']['event_count'] == 1
    assert observation['exact_metrics']['duration_sum_ms'] == 7.5
    assert observation['exact_metrics']['status_counts'] == {'503': 1}
    expected, _, _, _ = _compact_request(source.chunks[0], records_for_chunk(source.chunks[0]),
                                        SummaryConfig(), messages, **options)
    assert len(evidence['important_fields']) == len(expected['field_catalog'])


@pytest.mark.asyncio
async def test_escaped_unicode_repeated_values_and_cross_body_collisions():
    # Literal Unicode may not be synthesized from escaped source bytes.
    bodies = [r'{"label":"r\u00e9conciliation termin\u00e9e","code":731}',
              '{"label":"réconciliation terminée","code":732}',
              '{"outer":{"label":"réconciliation terminée"},"code":733}']
    events, source = await fixture([dict(message=body, event_id=f'structured-{i}',
        timestamp='2026-10-10T00:00:00Z') for i, body in enumerate(bodies)])
    messages, options = catalog_request_options(source, sidecar(events))
    catalog = options['source_catalog']
    exact = [r'r\u00e9conciliation termin\u00e9e', 'réconciliation terminée']
    for topic in exact:
        phrase, = [p for p in catalog.phrases if p.text == topic]
        assert phrase.event_keys == tuple(k for k, v in messages.items() if topic in v)
    _protect_bodies([p.text for p in catalog.phrases], [*messages.values(), *bodies])
    class UnicodeModel:
        local_only = preserve_content = True
        chat_model = 'structured-unicode-mock'
        calls = 0

        async def generate(self, instruction, payload, schema):
            self.calls += 1
            return select_topics(payload, exact, fields=True)

    model = UnicodeModel()
    compact = await compact_summary_batch(source, model, model_events=sidecar(events))
    assert model.calls == 1
    observations = validate_compact_chunk(compact.chunks[0])['source_observations']
    assert [o['text'] for o in observations] == exact
    assert [o['exact_metrics']['event_count'] for o in observations] == [1, 2]
    validate_refinement(source, compact)
    # A nonwitness body forbids a complete value phrase even with trusted values.
    views = {'one': bodies[1]}
    catalog = build_source_catalog(views, {'one': [bodies[1], exact[1]]},
                                   typed_values={_json('réconciliation terminée'), _json(732)})
    assert exact[1] not in {p.text for p in catalog.phrases}
    _protect_bodies([p.text for p in catalog.phrases], [bodies[1], exact[1]])


def test_exact_parser_nested_keys_arrays_escapes_and_delimiter_strings():
    body = r'prefix {"a":{"b":["brace } {",true,null,-1.2e3,"\u00e9"]},"c":"a\\b"}'
    typed = {_json(['brace } {', True, None, -1200.0, 'é']), _json('a\\b')}
    objects = _structured_spans(body, typed)
    assert len(objects) == 1
    whole, keys, leaves = objects[0]
    assert body[slice(*whole)] == body[7:]
    assert [body[a:b] for a, b in keys] == ['"a"', '"b"', '"c"']
    assert [body[a:b] for a, b in leaves] == [
        '"brace } {"', 'true', 'null', '-1.2e3', r'"\u00e9"', r'"a\\b"']
    assert _structured_spans(body, set()) == ()


@pytest.mark.parametrize('limit,body', [
    ('MAX_JSON_SCAN_CHARS', '{"a":"long value"}'),
    ('MAX_JSON_TOKENS', '{"a":[1,2,3]}'),
    ('MAX_JSON_DEPTH', '{"a":{"b":{"c":1}}}'),
])
def test_json_offset_work_bounds_fail_closed(monkeypatch, limit, body):
    import logchat.rag.source_catalog as module
    monkeypatch.setattr(module, limit, 2)
    with pytest.raises(ValueError, match='source_catalog_json_budget_exceeded'):
        build_source_catalog({'one': body}, {'one': [body]}, typed_values={'1'})


def test_key_only_safe_proposals_are_not_relabelled_impossible_omissions():
    body = '{"label":1}'
    with pytest.raises(ValueError, match='source_catalog_coverage_conflict'):
        build_source_catalog({'one': body}, {'one': [body]}, typed_values={'1'})


@pytest.mark.parametrize('body', ['{"a":truefalse}', '{"a":01}', '{"a":[1,}',
                                  '{"a":"bad\\q"}', '{"a":false,}'])
def test_invalid_json_has_no_structured_offsets(body):
    assert _structured_spans(body, {'false', 'true', '1'}) == ()
