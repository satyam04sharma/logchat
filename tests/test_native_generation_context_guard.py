"""Native guard regressions are mocked transport proofs, never model calls."""
import asyncio
import json

import httpx
import pytest

from pipeline.models import GenerationContextExceeded, LocalModels, ModelUnavailable


SHOW = {'details': {'format': 'gguf'}, 'capabilities': ['completion'], 'messages': []}


def transport(*, version='0.34.4', show=None, generation=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': version})
        if request.url.path == '/api/show':
            assert json.loads(request.content) == {'model': 'chosen'}
            return httpx.Response(200, json=SHOW if show is None else show)
        assert request.url.path == '/api/generate'
        return generation if generation is not None else httpx.Response(200, json={'response': '{}', 'done_reason': 'stop'})
    return httpx.MockTransport(handler)


def model(wire):
    return LocalModels(base_url='http://127.0.0.1:11434', chat_model='chosen',
                       preserve_content=True, transport=wire)


@pytest.mark.asyncio
async def test_verified_native_flags_and_metadata_rechecked_for_each_call():
    seen = []
    client = model(transport(seen=seen))
    for _ in range(2):
        assert await client.generate('Judge', {'source': '雪 null literal'}, {'type': 'object'}) == {}
    assert [r.url.path for r in seen] == ['/api/version', '/api/show', '/api/generate'] * 2
    for request in seen[2::3]:
        body = json.loads(request.content)
        assert body['shift'] is False and body['truncate'] is True
        assert body['options'] == {'temperature': 0, 'num_ctx': 8192, 'num_predict': 1200}
        assert body['prompt'] == '{"source":"雪 null literal"}'
        assert '_debug_render_only' not in body


@pytest.mark.asyncio
@pytest.mark.parametrize('routing', [
    {}, {'remote_host': ''}, {'remote_model': ''},
    {'remote_host': '', 'remote_model': ''},
])
async def test_local_routing_metadata_allows_generation(routing):
    seen = []
    assert await model(transport(show=dict(SHOW, **routing), seen=seen)).generate('Judge', {}, {}) == {}
    assert [r.url.path for r in seen] == ['/api/version', '/api/show', '/api/generate']


@pytest.mark.asyncio
@pytest.mark.parametrize('fields', [
    ('remote_host',), ('remote_model',), ('remote_host', 'remote_model'),
])
@pytest.mark.parametrize('value', [
    'https://PRIVATE_CANARY.example', ' ', '\n', None, False, True, 0, 1,
    0.0, {}, {'secret': 'PRIVATE_CANARY'}, [], ['PRIVATE_CANARY'],
])
@pytest.mark.parametrize('preserve_content', [False, True])
@pytest.mark.parametrize('empty_companion', [False, True])
async def test_remote_or_malformed_routing_never_submits_generation(fields, value, preserve_content, empty_companion):
    # Keep all other metadata admissible, including an absent/empty companion,
    # so rejection must come from routing rather than another backend check.
    routing = {'remote_host': '', 'remote_model': ''} if empty_companion else {}
    routing.update({field: value for field in fields})
    seen = []
    client = model(transport(show=dict(SHOW, **routing), seen=seen))
    client.preserve_content = preserve_content
    with pytest.raises(ModelUnavailable) as caught:
        await client.generate('Judge', {}, {})
    assert [r.url.path for r in seen] == ['/api/version', '/api/show']
    assert str(caught.value) == 'Local generation requires a model without remote routing metadata.'
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize('routing', [
    {'remote_host': 'https://PRIVATE_CANARY.example'},
    {'remote_model': 'PRIVATE_CANARY'},
    {'remote_host': 'https://PRIVATE_CANARY.example', 'remote_model': 'PRIVATE_CANARY'},
])
async def test_local_to_remote_metadata_change_blocks_next_generation_without_caching(routing):
    seen = []
    shows = iter([SHOW, dict(SHOW, **routing), SHOW])
    def handler(request):
        seen.append(request.url.path)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/show':
            return httpx.Response(200, json=next(shows))
        assert request.url.path == '/api/generate'
        return httpx.Response(200, json={'response': '{}', 'done_reason': 'stop'})
    client = model(httpx.MockTransport(handler))
    assert await client.generate('Judge', {}, {}) == {}
    with pytest.raises(ModelUnavailable) as caught:
        await client.generate('Judge', {}, {})
    assert str(caught.value) == 'Local generation requires a model without remote routing metadata.'
    assert seen == ['/api/version', '/api/show', '/api/generate', '/api/version', '/api/show']
    assert await client.generate('Judge', {}, {}) == {}
    assert seen == [
        '/api/version', '/api/show', '/api/generate', '/api/version', '/api/show',
        '/api/version', '/api/show', '/api/generate',
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['0.34.3', '0.34.5', '0.35.0', '1.0.0', '0.34.4-dev', None, True, 344, {}, []])
async def test_unverified_versions_never_submit_generation(version):
    seen = []
    with pytest.raises(ModelUnavailable):
        await model(transport(version=version, seen=seen)).generate('Judge', {}, {})
    assert [r.url.path for r in seen] == ['/api/version']


@pytest.mark.asyncio
@pytest.mark.parametrize('show', [
    {}, {'details': {'format': 'safetensors'}, 'capabilities': ['completion']},
    {'details': {'format': 'gguf'}, 'capabilities': ['embedding']},
    {'details': {'format': 'gguf'}, 'capabilities': 'completion'},
    dict(SHOW, messages=[{'role': 'user', 'content': 'PRIVATE_CANARY'}]),
    dict(SHOW, messages=None), dict(SHOW, messages={}),
])
async def test_unknown_backend_or_stored_history_never_submits_generation(show):
    seen = []
    with pytest.raises(ModelUnavailable) as caught:
        await model(transport(show=show, seen=seen)).generate('Judge', {}, {})
    assert [r.url.path for r in seen] == ['/api/version', '/api/show']
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.asyncio
async def test_known_native_overflow_has_fixed_safe_category():
    response = httpx.Response(400, json={'error':
        'the prompt is longer than the context length currently available to the model; PRIVATE_CANARY'})
    with pytest.raises(GenerationContextExceeded) as caught:
        await model(transport(generation=response)).generate('Judge', {}, {})
    assert str(caught.value) == 'Local model input exceeds its native context limit.'


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [
    httpx.Response(400, json={'error': 'PRIVATE_CANARY'}),
    httpx.Response(400, content=b'not JSON PRIVATE_CANARY'),
    httpx.Response(400, content=b'{"error":"the prompt is longer than the context length currently available to the model;", "error":"PRIVATE_CANARY"}'),
    httpx.Response(500, json={'error': 'the prompt is longer than the context length currently available to the model; PRIVATE_CANARY'}),
])
async def test_unknown_errors_remain_generic_without_raw_body(response):
    with pytest.raises(ModelUnavailable) as caught:
        await model(transport(generation=response)).generate('Judge', {}, {})
    assert not isinstance(caught.value, GenerationContextExceeded)
    assert 'PRIVATE' not in str(caught.value)


class Stream(httpx.AsyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.reads += 1
            yield b'x' * 128

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_error_reads_stop_at_bounded_diagnostic_budget():
    stream = Stream()
    with pytest.raises(ModelUnavailable):
        await model(transport(generation=httpx.Response(400, stream=stream))).generate('Judge', {}, {})
    assert stream.reads == 9 and stream.closed


@pytest.mark.asyncio
async def test_capability_response_is_bounded_before_decoding(monkeypatch):
    monkeypatch.setattr('pipeline.models.MAX_LOCAL_RESPONSE_BYTES', 256)
    stream = Stream()
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, stream=stream)
    with pytest.raises(ModelUnavailable, match='byte budget'):
        await model(httpx.MockTransport(handler)).generate('Judge', {}, {})
    assert seen == ['/api/version'] and stream.reads == 3 and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['/api/version', '/api/show', '/api/generate'])
async def test_cancellation_propagates_at_each_transport_boundary(endpoint):
    seen = []
    async def handler(request):
        seen.append(request.url.path)
        if request.url.path == endpoint:
            raise asyncio.CancelledError()
        return httpx.Response(200, json={'version': '0.34.4'} if request.url.path == '/api/version' else SHOW)
    with pytest.raises(asyncio.CancelledError):
        await model(httpx.MockTransport(handler)).generate('Judge', {}, {})
    assert seen[-1] == endpoint


@pytest.mark.asyncio
async def test_native_output_exhaustion_still_fails_closed():
    response = httpx.Response(200, json={'response': '{}', 'done_reason': 'length'})
    with pytest.raises(ModelUnavailable, match='output budget'):
        await model(transport(generation=response)).generate('Judge', {}, {})


@pytest.mark.asyncio
async def test_daemon_version_change_does_not_reuse_prior_verification():
    seen = []
    versions = iter(['0.34.4', '0.34.3'])
    def handler(request):
        seen.append(request.url.path)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': next(versions)})
        if request.url.path == '/api/show':
            return httpx.Response(200, json=SHOW)
        return httpx.Response(200, json={'response': '{}'})
    client = model(httpx.MockTransport(handler))
    assert await client.generate('Judge', {}, {}) == {}
    with pytest.raises(ModelUnavailable, match='verified Ollama context guard'):
        await client.generate('Judge', {}, {})
    assert seen == ['/api/version', '/api/show', '/api/generate', '/api/version']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'redirect', 'duplicate', 'invalid'])
async def test_metadata_failures_and_redirects_never_submit_generation(failure):
    seen = []
    def handler(request):
        seen.append(request.url.path)
        if failure == 'timeout':
            raise httpx.ReadTimeout('PRIVATE_CANARY', request=request)
        if failure == 'redirect':
            return httpx.Response(302, headers={'location': 'http://example.test/PRIVATE_CANARY'})
        if failure == 'duplicate':
            return httpx.Response(200, content=b'{"version":"0.34.3","version":"0.34.4"}')
        return httpx.Response(200, json=['PRIVATE_CANARY'])
    with pytest.raises(ModelUnavailable) as caught:
        await model(httpx.MockTransport(handler)).generate('Judge', {}, {})
    assert seen == ['/api/version']
    assert 'PRIVATE' not in str(caught.value)
