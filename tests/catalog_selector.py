"""Protocol-aware positive mock: missing intended topics fail loudly."""
from logchat.rag.compact_input import expand_compact_input
from logchat.rag.source_catalog import SELECTOR_PROTOCOL


def select_topics(payload, topics, *, fields=False, allow_absent_topics=False):
    if 'input_projection' in payload:
        payload = expand_compact_input(payload)
    assert payload['selector_protocol'] == SELECTOR_PROTOCOL
    selected = []
    for topic in topics:
        intended = [row['ref'] for row in payload['records'] if topic in row['message']]
        if not intended:
            assert allow_absent_topics, 'Required topic absent from supplied records'
            continue  # Explicitly permitted for topics in other packed requests.
        candidates = [(ref, text) for ref, text in payload['phrase_catalog']
                      if topic in text and [row['ref'] for row in payload['records']
                                             if text in row['message']] == intended]
        assert candidates, 'Required topic unavailable in supplied catalog'
        ref, _ = min(candidates, key=lambda item: (len(item[1]), item[0]))
        selected.append(ref)
    return {'selections': role_selections(selected),
            'important_fields': [f['field_ref'] for f in payload['field_catalog']] if fields else []}


def role_selections(refs):
    """V4 selections are the identical plain refs; no selection/ranking change."""
    return refs
