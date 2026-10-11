"""Complete projected request proofs using inert models and counters only."""
from copy import deepcopy

import pytest

from logchat.rag.compact_bounds import compact_reservations
from logchat.rag.compact_input import COMPACT_INPUT_VERSION, expand_compact_input
from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, COMPACT_INSTRUCTION, SectionModelError,
    SummaryConfig, _compact_request, _expanded_compact_request,
    compact_summary_batch, records_for_chunk, validate_refinement,
)
from pipeline.model_transport import generation_body, model_json
from pipeline.token_budget import request_digest
from tests.test_catalog_compact_preflight import fixture
from tests.test_rag_compact import CompactModel


@pytest.mark.asyncio
@pytest.mark.parametrize('catalog', [False, True])
async def test_factory_retains_complete_typed_input_schema_and_reservations(catalog):
    source, views, options = await fixture(['operation failed with rare detail'] * 4)
    chunk = source.chunks[0]
    args = (chunk, records_for_chunk(chunk), SummaryConfig(), views)
    options = options if catalog else {}
    original, schema, metadata, fields = _expanded_compact_request(*args, **options)
    projected, projected_schema, projected_metadata, projected_fields = _compact_request(*args, **options)
    assert model_json(expand_compact_input(projected)) == model_json(original)
    assert (projected_schema, projected_metadata, projected_fields) == (schema, metadata, fields)
    assert compact_reservations(projected, metadata, args[2]) == compact_reservations(original, metadata, args[2])
    assert list(projected['message_dictionary'].values()) == list(dict.fromkeys(views.values()))
    assert COMPACT_INPUT_VERSION in (CATALOG_COMPACT_INSTRUCTION if catalog else COMPACT_INSTRUCTION)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['wrongref', 'substitution', 'typed', 'missing', 'version', 'choice'])
async def test_corrupt_projection_fails_before_any_physical_check_or_generation(monkeypatch, mutation):
    import logchat.rag.compact_input as codec
    source, _, _ = await fixture(['operation failed with rare detail'] * 4)
    project = codec.project_compact_input

    corrupted = False

    def corrupt(original):
        nonlocal corrupted
        result = project(original)
        if corrupted:
            return result
        corrupted = True
        if mutation == 'wrongref':
            result['records'][0]['message_ref'] = 'm999'
        elif mutation == 'substitution':
            result['message_dictionary']['m0'] = 'substituted source'
        elif mutation == 'typed':
            result['records'][0]['duration_ms'] = False
        elif mutation == 'missing':
            result['records'].pop()
        elif mutation == 'version':
            result['input_projection'] = 'unknown'
        else:
            result['phrase_catalog'].pop()
        return result

    monkeypatch.setattr(codec, 'project_compact_input', corrupt)

    class Never(CompactModel):
        async def preflight_generation(self, *args):
            pytest.fail('Invalid projection reached physical preflight')

    model = Never()
    with pytest.raises(SectionModelError, match='^compact_input_projection_invalid:compact_summary$'):
        await compact_summary_batch(source, model)
    assert model.calls == 0


@pytest.mark.asyncio
async def test_all_final_projected_requests_proved_before_first_generation():
    source, _, _ = await fixture(['operation failed with rare detail'] * 7)

    class ProofModel(CompactModel):
        def __init__(self):
            super().__init__('empty_fields')
            self.proofs, self.checked, self.sent = set(), [], []

        async def preflight_generation(self, instruction, payload, schema):
            assert self.calls == 0
            assert instruction == CATALOG_COMPACT_INSTRUCTION
            assert payload['input_projection'] == COMPACT_INPUT_VERSION
            expanded = expand_compact_input(payload)
            assert len(expanded['records']) == len(payload['records'])
            body = generation_body(self.chat_model, instruction, payload, schema)
            self.checked.append(deepcopy(body))
            # Explicit nonmonotonic synthetic predicate: no token estimate.
            fits = len(payload['records']) in {1, 2, 4}
            if fits:
                self.proofs.add(request_digest(body))
            return fits

        async def generate(self, instruction, payload, schema):
            body = generation_body(self.chat_model, instruction, payload, schema)
            assert request_digest(body) in self.proofs
            assert body in self.checked
            self.sent.append(deepcopy(body))
            return await super().generate(instruction, payload, schema)

    model = ProofModel()
    compact = await compact_summary_batch(source, model)
    validate_refinement(source, compact)
    assert [len(p['records']) for p in model.wire_payloads] == [1, 2, 4]
    assert sum(len(p['records']) for p in model.wire_payloads) == 7
    assert len(model.sent) == 3
    assert all(len(p['message_dictionary']) == 1 for p in model.wire_payloads)


@pytest.mark.asyncio
@pytest.mark.parametrize('catalog', [False, True])
async def test_exact_counter_and_generation_hook_receive_projected_complete_body(catalog):
    import json
    import httpx
    from pipeline.models import LocalModels
    from tests.test_compact_token_planning import Counter

    source, views, options = await fixture(['operation failed with rare detail'] * 4)
    chunk = source.chunks[0]
    instruction = CATALOG_COMPACT_INSTRUCTION if catalog else COMPACT_INSTRUCTION
    selection_key = 'selections' if catalog else 'source_phrases'
    response = {selection_key: [], 'important_fields': []}
    payload, schema, _, _ = _compact_request(
        chunk, records_for_chunk(chunk), SummaryConfig(), views, **(options if catalog else {}))
    sent = []

    def handler(request):
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'}, 'capabilities': ['completion']})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'response': json.dumps(response)})

    counter = Counter()
    model = LocalModels(base_url='http://localhost:11434', chat_model='mistral:7b', preserve_content=True,
                        generation_token_counter=counter, transport=httpx.MockTransport(handler))
    assert await model.preflight_generation(instruction, payload, schema)
    assert await model.generate(instruction, payload, schema) == response
    expected = generation_body(model.chat_model, instruction, payload, schema)
    assert counter.requests == [expected, expected]
    assert sent == [expected]
    assert json.loads(expected['prompt'])['input_projection'] == COMPACT_INPUT_VERSION
    # Contract delivery only: an inert response does not prove model usefulness.
    policy = (
        'When safe eligible phrases exist, select quotes that jointly identify the operation\n'
        'or subject and what happened. Prefer one informative combined phrase or\n'
        'complementary phrases; retain meaningful multiword actions over isolated generic\n'
        'words or partial actions. Preserve distinctive context of rare failures.\n'
        'Source assertions remain attributed quotes, never established explanations.\n'
    )
    assert expected['system'].count(policy) == 1
    without_policy = generation_body(model.chat_model, instruction.replace(policy, ''), payload, schema)
    assert request_digest(without_policy) != request_digest(expected)
    assert schema['properties'][selection_key]['maxItems'] == 4
    assert payload['combined_field_limit'] == 64
