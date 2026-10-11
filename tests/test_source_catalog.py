"""Offline catalog proposals, privacy, exact bindings and transport controls."""
from dataclasses import replace
from itertools import combinations

import pytest

from logchat.rag.source_catalog import (
    build_source_catalog, select_catalog_observations,
)
from logchat.rag.source_observations import _protect_bodies, _json
from test_source_observations import fixture


def source(messages, extra=None):
    records, views, bodies = fixture(messages)
    for index, row in enumerate(records):
        row['event_key'] = f'key{index}'
        bodies[row['ref']].extend((extra or {}).get(index, []))
    catalog = build_source_catalog(
        {row['event_key']: views[row['ref']] for row in records},
        {row['event_key']: bodies[row['ref']] for row in records})
    return records, views, bodies, catalog


def select(data, refs, **changes):
    value = {'phrase_refs': refs, 'important_fields': []}
    value.update(changes)
    records, views, bodies, catalog = data
    return select_catalog_observations(value, records, views, bodies, ['f0'], catalog,
                                       output_budget=8000)


def test_repeated_views_have_one_physical_text_and_complete_membership():
    data = source(['worker operation completed with status'] * 35)
    records, views, bodies, catalog = data
    bound = catalog.bind(records, views, bodies)
    assert bound and not catalog.omissions
    assert len({item['text'] for item in bound}) == len(bound)
    for item in bound:
        assert item['record_ref'] == 'e0'
        assert item['event_refs'] == list(views)
        assert item['text'] in views['e0'] and len(item['text']) <= 120
        observations, _ = select(data, [item['phrase_ref']])
        assert observations[0]['exact_metrics']['event_count'] == 35
        assert observations[0]['event_refs'] == list(views)
    _protect_bodies([item['text'] for item in bound], list(views.values()))


def test_all_subsets_and_all_body_surfaces_are_safe():
    data = source(['worker task finished after delay', 'auth credential denied during rotation'],
                  {0: ['worker task finished', 'prefix worker task finished after delay'],
                   1: ['auth credential denied']})
    catalog = data[-1]
    texts = [item.text for item in catalog.phrases]
    bodies = [*data[1].values(), *(body for values in data[2].values() for body in values)]
    assert texts
    _protect_bodies(texts, bodies)
    for count in range(min(4, len(texts)) + 1):
        for subset in combinations(texts, count):
            _protect_bodies(subset, bodies)


@pytest.mark.parametrize('gap', [' ', '\t', '\n', '—', '…', ' / ', '()'])
def test_global_nonwitness_repeated_and_separator_protection(gap):
    data = source(['alpha operation extra', 'omega operation extra',
                   f'alpha{gap}alpha{gap}omega{gap}omega'])
    catalog = data[-1]
    _protect_bodies([item.text for item in catalog.phrases], list(data[1].values()))
    # A catalog built for the entire batch stays safe after any future split.
    for index, record in enumerate(data[0]):
        local = [{**record, 'ref': 'e0'}]
        bound = catalog.bind(local, {'e0': data[1][f'e{index}']},
                             {'e0': data[2][f'e{index}']})
        assert bound or record['event_key'] in catalog.omissions
        assert all(item['event_refs'] == ['e0'] for item in bound)


def test_rare_tail_pattern_coverage_and_exact_facts():
    data = source(['routine directory listing served successfully'] * 32
                  + ['rare reconciliation job exhausted retry budget after delay'])
    catalog = data[-1]
    assert not catalog.omissions
    rare = [item for item in catalog.phrases if item.event_keys == ('key32',)]
    assert rare
    observations, _ = select(data, [rare[0].phrase_ref])
    assert observations[0]['event_refs'] == ['e32']
    assert observations[0]['exact_metrics']['event_count'] == 1
    assert observations[0]['observed_window']['span_ms'] == 0


def test_atomic_omission_is_explicit_and_does_not_remove_records():
    data = source(['atomic', 'ordinary operation finished with delay'])
    assert data[-1].omissions == ('key0',)
    assert dict(data[-1].views)['key0'] == 'atomic'
    assert len(data[0]) == 2
    assert data[-1].phrases
    assert select(data, [data[-1].phrases[0].phrase_ref])[0]


def test_unrepresentable_global_coverage_conflict_fails_preflight():
    # Each source has an individually safe whole-word proposal. Together the
    # producer exclusions forbid any way to cover both source patterns.
    with pytest.raises(ValueError, match='coverage_conflict'):
        source(['alpha one', 'beta two'], {0: ['one', 'two', 'alpha beta']})


@pytest.mark.parametrize('refs', [None, 'p0', [None], [False], [0], [[]], [{}],
                                 ['unknown'], ['p0', 'unknown'], ['p0'] * 5])
def test_invalid_incoming_refs_reject_before_any_normalization(refs):
    with pytest.raises(ValueError, match='invalid_source_catalog_refs'):
        select(source(['operation completed after delay']), refs)


def test_exact_duplicate_refs_normalize_only_after_full_validation():
    data = source(['operation completed after delay'])
    ref = data[-1].phrases[0].phrase_ref
    assert select(data, [ref] * 4) == select(data, [ref])
    with pytest.raises(ValueError):
        select(data, [ref, ref, 'unknown'])
    for change in ({'important_fields': ['f0', 'f0']}, {'important_fields': [False]},
                   {'important_fields': ['missing']}, {'summary': 'invented'},
                   {'source_phrases': []}):
        with pytest.raises(ValueError):
            select(data, [ref, ref], **change)


def test_transport_budget_counts_actual_ref_response():
    records, views, bodies, catalog = source(['operation completed after delay'])
    value = {'phrase_refs': [catalog.phrases[0].phrase_ref], 'important_fields': []}
    budget = len(_json(value))
    select_catalog_observations(value, records, views, bodies, [], catalog, output_budget=budget)
    with pytest.raises(ValueError, match='invalid_source_catalog_output'):
        select_catalog_observations(value, records, views, bodies, [], catalog, output_budget=budget - 1)


@pytest.mark.parametrize('mutation', ['view', 'body', 'key', 'duplicate_key', 'membership'])
def test_source_bindings_fail_closed(mutation):
    records, views, bodies, catalog = source(['operation completed after delay'] * 2)
    if mutation == 'view':
        views['e0'] += ' altered'
    elif mutation == 'body':
        bodies['e0'].append('other protected surface')
    elif mutation == 'key':
        records[0]['event_key'] = 'unknown'
    elif mutation == 'duplicate_key':
        records[0]['event_key'] = records[1]['event_key']
    else:
        catalog = replace(catalog, phrases=(replace(catalog.phrases[0], event_keys=('key0',)),))
    with pytest.raises(ValueError, match='source_catalog_'):
        catalog.bind(records, views, bodies)


def test_proposal_and_catalog_limits_reject_instead_of_trimming(monkeypatch):
    import logchat.rag.source_catalog as module
    monkeypatch.setattr(module, 'MAX_PROPOSALS', 2)
    with pytest.raises(ValueError, match='proposal_budget_exceeded'):
        source(['operation completed after delay'])
    monkeypatch.setattr(module, 'MAX_PROPOSALS', 65536)
    monkeypatch.setattr(module, 'MAX_CATALOG_PHRASES', 1)
    with pytest.raises(ValueError, match='size_exceeded'):
        source(['operation completed after delay'])


def test_unicode_and_punctuation_bytes_and_membership_are_exact():
    data = source(['réconciliation—job terminé avec délai',
                   'réconciliation\tjob terminé avec délai'])
    catalog = data[-1]
    assert catalog.phrases
    for item in catalog.phrases:
        assert item.event_keys == tuple(key for key, view in catalog.views if item.text in view)
    _protect_bodies([item.text for item in catalog.phrases], list(data[1].values()))


def test_exhaustive_every_subset_of_small_catalog():
    data = source(['alpha beta gamma'])
    texts = [item.text for item in data[-1].phrases]
    assert 0 < len(texts) <= 8
    for count in range(len(texts) + 1):
        for subset in combinations(texts, count):
            _protect_bodies(subset, list(data[1].values()))


def test_falsely_empty_membership_cannot_hide_a_matching_catalog_phrase():
    records, views, bodies, catalog = source(['operation completed after delay'])
    catalog = replace(catalog, phrases=(replace(catalog.phrases[0], event_keys=()),))
    with pytest.raises(ValueError, match='membership_mismatch'):
        catalog.bind(records, views, bodies)


@pytest.mark.parametrize('connective', ['after', 'before', 'during', 'with', 'without',
                                        'through', 'AND', 'while', 'by'])
@pytest.mark.parametrize('left,right', [
    ('credential denied', 'signing key rotation'),
    ('quartz transfer stalled', 'ledger shard rollover'),
    ('not delivered', 'parcel routing failure'),
])
def test_connective_gaps_keep_both_content_spans(connective, left, right):
    data = source([f'{left} {connective} {right}'] * 35)
    texts = {item.text for item in data[-1].phrases}
    assert left in texts and right in texts
    assert not any(connective in text for text in texts)
    assert not data[-1].omissions
    _protect_bodies(texts, list(data[1].values()))


@pytest.mark.parametrize('message,anchors', [
    ('Upstream payment gateway connection timed out', ['payment gateway', 'timed out']),
    ('Payment reconciliation job exhausted retry budget',
     ['reconciliation job', 'exhausted retry budget']),
    ('Service credential denied after signing key rotation',
     ['credential', 'denied', 'signing', 'key rotation']),
])
def test_mixed_rare_incident_content_candidates_remain_available(message, anchors):
    data = source(['routine directory listing served with success'] * 32 + [message])
    assert not data[-1].omissions
    for anchor in anchors:
        assert any(anchor in item.text and 'key32' in item.event_keys
                   for item in data[-1].phrases)
    _protect_bodies([item.text for item in data[-1].phrases], list(data[1].values()))


@pytest.mark.parametrize('message', [
    'réconciliation terminée délai dépassé', '处理 失败 任务 重试',
    'alpha beta gamma delta', 'and or with', 'alpha after alpha after beta',
])
def test_fallback_and_repeated_word_coverage_is_safe_and_deterministic(message):
    first = source([message])[-1]
    second = source([message])[-1]
    assert first == second
    assert first.phrases and not first.omissions
    _protect_bodies([item.text for item in first.phrases], [message])


def test_reserved_gap_conflict_fails_instead_of_claiming_infeasibility_or_omission():
    # "he" is content in one pattern but also covers bytes inside the reserved
    # "the" gap of a different body. There is no semantic exception for it.
    with pytest.raises(ValueError, match='source_catalog_coverage_conflict'):
        source(['he she', 'alpha the beta'], {0: ['she']})


def test_all_connective_positions_and_nonwitness_body_variants_are_reserved():
    data = source(['alpha after beta before gamma with delta',
                   'omega through sigma'],
                  {1: ['alpha after beta', 'gamma with delta']})
    texts = {item.text for item in data[-1].phrases}
    assert {'alpha', 'beta', 'gamma', 'delta', 'omega', 'sigma'} <= texts
    assert not any(word in text.split() for text in texts
                   for word in ('after', 'before', 'with', 'through'))
    _protect_bodies(texts, [*data[1].values(),
                           *(body for values in data[2].values() for body in values)])
