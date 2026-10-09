"""Wire limits must apply before decoding and stop reading oversized streams."""
import json

import httpx
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
    model = LocalModels(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    with pytest.raises(ModelUnavailable, match='byte budget'):
        await model.generate('Select context', {}, {'type': 'object'})
    assert stream.reads == 3
    assert stream.closed


@pytest.mark.asyncio
async def test_installed_model_listing_is_bounded_and_returns_unavailable(monkeypatch):
    monkeypatch.setattr('pipeline.models.MAX_LOCAL_RESPONSE_BYTES', 256)
    stream = OversizedStream()
    model = LocalModels(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    status = await model.installed()
    assert not status['chat'] and not status['embedding']
    assert stream.reads == 3 and stream.closed


@pytest.mark.asyncio
async def test_small_valid_generation_remains_compatible():
    body = {'response': json.dumps({'context': 'Observed failure'})}
    model = LocalModels(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    assert await model.generate('Select context', {}, {'type': 'object'}) == {'context': 'Observed failure'}


@pytest.mark.asyncio
async def test_non_object_response_has_safe_error():
    model = LocalModels(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=['PRIVATE_CANARY'])))
    with pytest.raises(ModelUnavailable, match='invalid response object') as caught:
        await model.generate('Select context', {}, {'type': 'object'})
    assert 'PRIVATE_CANARY' not in str(caught.value)
