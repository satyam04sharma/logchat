"""Pure manifest mutations using synthetic counts; no runtime or model assets."""
from dataclasses import replace
import json

import pytest

from logchat.rag.embedding_subdivision import validate_embedding_plan
from tests.test_embedding_subdivision import fixture
from tests.test_embedding_subdivision_planner import plan, setup


@pytest.mark.asyncio
@pytest.mark.parametrize('split', [False, True])
@pytest.mark.parametrize('section,field', [
    ('leaf', 'parent_index'),
    ('leaf', 'record_start'),
    ('leaf', 'record_end'),
    ('document', 'input_bytes'),
    ('request', 'first_document'),
    ('request', 'document_count'),
])
async def test_manifest_integer_boundaries(section, field, split, subtests):
    def measure(text):
        records = json.loads(text.removeprefix('search_document: '))[
            'whole_section_facts']['exact_metrics']['event_count']
        return 2049 if split and records > 1 else 2048

    original = fixture()
    provider, models, counter = setup(measure)
    result = await plan(original, provider, models)
    assert counter.closed
    assert validate_embedding_plan(original, result, provider) == result.lineage
    assert result.physical_flags == (True, True)
    assert all(type(flag) is bool for flag in result.physical_flags)
    items = {'leaf': result.lineage.leaves, 'document': result.documents,
             'request': result.requests}[section]
    # Include later leaves: a forged start/end can overlap a preceding interval.
    for index, item in enumerate(items):
        value = getattr(item, field)
        assert type(value) is int
        for invalid in (False, True, float(value), -1, 0, value + 1, 999_999):
            if type(invalid) is int and invalid == value:
                continue  # Zero is valid for the first parent/start/request index.
            with subtests.test(index=index, field=field, value=repr(invalid)):
                changed = list(items)
                changed[index] = replace(item, **{field: invalid})
                if section == 'leaf':
                    altered = replace(result, lineage=replace(
                        result.lineage, leaves=tuple(changed)))
                else:
                    altered = replace(result, **{section + 's': tuple(changed)})
                with pytest.raises(ValueError, match='subdivision_.*_mismatch'):
                    validate_embedding_plan(original, altered, provider)
