"""Wire limits must apply before decoding and stop reading oversized streams."""
import json

import httpx
from test_native_model_support import guarded_transport
import pytest

from pipeline.models import LocalModels, ModelUnavailable


class OversizedStream(httpx.AsyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(20):
            self.reads += 1
            yield b'x' * 128

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_generation_stops_at_wire_budget_without_content_length(monkeypatch):
    monkeypatch.setattr('pipeline.models.MAX_LOCAL_RESPONSE_BYTES', 256)
    stream = OversizedStream()
    model = LocalModels(transport=guarded_transport(lambda request: httpx.Response(200, stream=stream)))
    with pytest.raises(ModelUnavailable, match='byte budget'):
        await model.generate('Select context', {}, {'type': 'object'})
    assert stream.reads == 3
    assert stream.closed


@pytest.mark.asyncio
async def test_installed_model_listing_is_bounded_and_returns_unavailable(monkeypatch):
    monkeypatch.setattr('pipeline.models.MAX_LOCAL_RESPONSE_BYTES', 256)
    stream = OversizedStream()
    model = LocalModels(transport=guarded_transport(lambda request: httpx.Response(200, stream=stream)))
    status = await model.installed()
    assert not status['chat'] and not status['embedding']
    assert stream.reads == 3 and stream.closed


@pytest.mark.asyncio
async def test_small_valid_generation_remains_compatible():
    body = {'response': json.dumps({'context': 'Observed failure'})}
    model = LocalModels(transport=guarded_transport(lambda request: httpx.Response(200, json=body)))
    assert await model.generate('Select context', {}, {'type': 'object'}) == {'context': 'Observed failure'}


@pytest.mark.asyncio
async def test_non_object_response_has_safe_error():
    model = LocalModels(transport=guarded_transport(lambda request: httpx.Response(200, json=['PRIVATE_CANARY'])))
    with pytest.raises(ModelUnavailable, match='invalid response object') as caught:
        await model.generate('Select context', {}, {'type': 'object'})
    assert 'PRIVATE_CANARY' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('preserve_content', [False, True])
@pytest.mark.parametrize('content', [
    '{"judgements":{"c0":{"selected":false},"c0":{"selected":true}}}',
    '{"judgements":{"c0":{"selected":false,"selected":true}}}',
    r'{"judgements":{"c0":{"selected":false},"\u00630":{"selected":true}}}',
    '{"items":[{"PRIVATE_KEY_CANARY":false,"PRIVATE_KEY_CANARY":true}]}',
    '{"context":"PRIVATE_VALUE_CANARY","context":"accepted"}',
])
async def test_duplicate_generated_keys_fail_before_schema_or_sanitization(content, preserve_content):
    # These responses all become acceptable objects with plain json.loads;
    # a permissive schema ensures rejection comes from decoding itself.
    assert isinstance(json.loads(content), dict)
    model = LocalModels(base_url='http://127.0.0.1:11434', preserve_content=preserve_content,
        transport=guarded_transport(lambda request: httpx.Response(200, json={
            'response': content, 'done_reason': 'stop',
        })))
    with pytest.raises(ModelUnavailable, match='invalid structured output') as caught:
        await model.generate('Judge supplied candidates', {}, {'type': 'object'})
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    b'{"response":"{}","done_reason":"length","done_reason":"stop"}',
    b'{"response":"not JSON","response":"{}","done_reason":"stop"}',
])
async def test_duplicate_wire_keys_cannot_overwrite_response_or_termination(body):
    model = LocalModels(transport=guarded_transport(lambda request: httpx.Response(200, content=body)))
    with pytest.raises(ModelUnavailable):
        await model.generate('Judge supplied candidates', {}, {'type': 'object'})


@pytest.mark.asyncio
async def test_distinct_objects_can_share_local_refs_and_values_without_repair():
    content = {'judgements': {'c0': {'selected': True}, 'c1': {'selected': False}},
               'records': [{'ref': 'e0', 'request_status': 200}, {'ref': 'e0', 'request_status': 200}],
               'source': {'ref': 'content'}}
    model = LocalModels(base_url='http://127.0.0.1:11434', preserve_content=True,
        transport=guarded_transport(lambda request: httpx.Response(200, json={
            'response': json.dumps(content), 'done_reason': 'stop',
        })))
    assert await model.generate('Judge supplied candidates', {}, {'type': 'object'}) == content
