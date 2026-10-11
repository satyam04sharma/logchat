"""Serialization bounds for accepted v3 selectors, not hypothetical prose.

Costs include each JSON envelope. Independent character/UTF-8 maxima may choose
different optional fields. No source text is returned or made durable here.
"""
from datetime import datetime

from logchat.rag.source_observations import (
    EVIDENCE_VERSION, RENDER_VERSION, MAX_PHRASES, MAX_PHRASE_CHARS,
    _facts, _json, observation_embedding,
)


def compact_reservations(payload, records, config):
    from logchat.rag.source_catalog import SELECTOR_PROTOCOL
    from logchat.rag.compact_input import expand_compact_input
    if "input_projection" in payload:
        payload = expand_compact_input(payload)
    protocol = payload.get('selector_protocol')
    if 'selector_protocol' in payload and protocol not in ('source-catalog-selector-v1', SELECTOR_PROTOCOL):
        raise ValueError('unknown_compact_selector_protocol')
    catalog = payload['phrase_catalog'] if protocol in ('source-catalog-selector-v1', SELECTOR_PROTOCOL) else None
    fields = payload['field_catalog']
    required = set(payload['required_field_refs'])
    slots = 64 - len(required)

    def cost(value, byte=False):
        text = _json(value)
        return len(text.encode('utf-8')) if byte else len(text)

    def field_array(byte, semantic=False, refs_only=False):
        # V2 reserves the original incoming list, before known-ref deduplication.
        # Historical v1 and durable/embedding arrays retain distinct-field bounds.
        if refs_only and protocol == SELECTOR_PROTOCOL:
            largest = max((cost(f["field_ref"], byte) for f in fields), default=0)
            return 2 + 64 * (largest + 1) - 1 if fields else 2
        def item(field):
            if refs_only:
                return field['field_ref']
            keys = ('key', 'value') if semantic else ('key', 'value', 'event_refs')
            return {key: field[key] for key in keys}
        mandatory = [cost(item(f), byte) + 1 for f in fields if f['field_ref'] in required]
        optional = sorted((cost(item(f), byte) + 1 for f in fields if f['field_ref'] not in required), reverse=True)
        selected = mandatory + optional[:slots]
        return 2 + sum(selected) - bool(selected)

    def phrase_cost(byte):
        if catalog is not None:
            return max((cost(item[1], byte) for item in catalog), default=2)
        # Every accepted phrase is a contiguous <=120-character source substring.
        # A rolling additive JSON cost safely includes escaping and UTF-8; strings
        # are not normalized. Max over views, not an impossible concatenation.
        maximum = 0
        for row in payload['records']:
            weights = [cost(char, byte) - 2 for char in row['message']]
            running = 0
            for i, weight in enumerate(weights):
                running += weight
                if i >= MAX_PHRASE_CHARS:
                    running -= weights[i - MAX_PHRASE_CHARS]
                maximum = max(maximum, running)
        return maximum + 2

    # All nonnegative finite binary64 subset sums/spans serialize in <=24 ASCII
    # characters (including exponent). Extrema are actual converted source values
    # or null. A whole-section sum is NOT a bound on subset JSON spelling.
    whole = _facts(records)
    metrics = whole['exact_metrics']
    subset = {'exact_metrics': {**metrics, 'duration_sum_ms': None},
              'observed_window': {**whole['observed_window'], 'span_ms': None}}
    refs = [row['ref'] for row in records]
    longest_ref = max(refs, key=len)
    stamps = [datetime.fromisoformat(row['timestamp']).isoformat() for row in records]
    stamp = max(stamps, key=lambda x: cost(x))
    subset['observed_window'].update(start=stamp, end=stamp)
    durations = [None, *[float(row['duration_ms']) for row in records if row['duration_ms'] is not None]]
    longest_duration = max(durations, key=lambda x: cost(x))
    subset['exact_metrics'].update(duration_min_ms=longest_duration, duration_max_ms=longest_duration)
    # Counts and each status-map entry are nonnegative subset counts, hence no
    # more decimal digits than whole counts. Nulls in the skeleton stand in for
    # arbitrary subset float sums and spans; replace their cost below.
    float_extra = 2 * (24 - len('null'))
    observation = {'record_ref': longest_ref, 'text': '', 'event_refs': refs, **subset}
    semantic_observation = {'text': '', **subset}
    evidence = {'version': EVIDENCE_VERSION, 'render_version': RENDER_VERSION,
                'records': records, 'important_fields': [], 'source_observations': []}
    embedding = observation_embedding([], records, [])
    embedding['summary'] = ''
    selector = {'source_phrases' if catalog is None else 'phrase_refs': [], 'important_fields': []}
    phrase_count = MAX_PHRASES if catalog is None else min(MAX_PHRASES, len(catalog))
    # Exact redundant references are allowed, but never expand into additional
    # observations. Even a one-item catalog can receive four incoming refs.
    incoming_count = MAX_PHRASES if catalog is None or catalog else 0
    bounds = {}
    for byte, suffix in ((False, 'chars'), (True, 'bytes')):
        phrase = phrase_cost(byte)
        expanded = cost(observation, byte) - cost('', byte) + phrase + float_extra
        semantic = cost(semantic_observation, byte) - cost('', byte) + phrase + float_extra
        selector_item = cost({'record_ref': longest_ref, 'text': ''}, byte) - 2 + phrase
        if catalog is not None:
            selector_item = max((cost(item[0], byte) for item in catalog), default=0)
        # Final canonical rendering is an acceptance predicate: reserve its cap,
        # not a worst escaped phrase combination that that predicate rejects.
        summary = config.summary_character_budget * (4 if byte else 1)
        bounds['evidence_' + suffix] = (cost(evidence, byte) - 4 + field_array(byte)
                                        + 2 + phrase_count * (expanded + 1) - bool(phrase_count))
        bounds['embedding_' + suffix] = (cost(embedding, byte) - 6 + summary + field_array(byte, semantic=True)
                                         + 2 + phrase_count * (semantic + 1) - bool(phrase_count))
        bounds['selector_' + suffix] = (cost(selector, byte) - 4 + field_array(byte, refs_only=True)
                                        + 2 + incoming_count * (selector_item + 1) - bool(incoming_count))
    return bounds
