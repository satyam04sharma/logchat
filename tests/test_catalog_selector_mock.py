"""Positive mock failures stay visible instead of becoming empty selections."""
import pytest

from logchat.rag.source_catalog import SELECTOR_PROTOCOL
from tests.catalog_selector import select_topics


def payload(catalog):
    return {
        "selector_protocol": SELECTOR_PROTOCOL,
        "records": [{"ref": "e0", "message": "dispatch delayed east"},
                    {"ref": "e1", "message": "dispatch delayed west"}],
        "phrase_catalog": catalog,
        "field_catalog": [],
    }


def test_positive_mock_requires_topic_in_records():
    with pytest.raises(AssertionError, match="absent from supplied records"):
        select_topics(payload([["p0", "dispatch delayed"]]), ["missing topic"])


@pytest.mark.parametrize("catalog", [[], [["p0", "dispatch"]], [["p0", "dispatch delayed east"]]])
def test_positive_mock_requires_offered_topic_with_complete_membership(catalog):
    with pytest.raises(AssertionError, match="unavailable in supplied catalog"):
        select_topics(payload(catalog), ["dispatch delayed"])


def test_positive_mock_selects_exact_topic_membership_not_first_item():
    result = select_topics(payload([["p0", "east"], ["p1", "dispatch delayed"]]), ["dispatch delayed"])
    assert result == {"phrase_refs": ["p1"], "important_fields": []}


def test_other_packed_request_requires_explicit_absence_permission():
    assert select_topics(payload([]), ["other section"], allow_absent_topics=True) == {
        "phrase_refs": [], "important_fields": []}
