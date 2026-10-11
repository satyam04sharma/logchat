"""Controlled HTTP/IPC regression tests; no installed assets or network calls."""
import asyncio
import json
import socket

import httpx
import pytest

from pipeline.models import LocalModels, ModelUnavailable
from tests.test_default_native_counter import installation, reaped
from tests.test_native_model_support import guarded_transport


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('offline network denied')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def mutate(client, mutation, body=None):
    if mutation == 'endpoint':
        client.base_url = 'https://outside.example.invalid'
    elif mutation == 'transport':
        client.transport = httpx.MockTransport(lambda request: pytest.fail('changed transport used'))
    elif mutation == 'model':
        client.chat_model = 'private-source-path-unreviewed'
    elif mutation == 'body':
        body['prompt'] = 'private-source-path-mutated'
    elif mutation == 'context':
        body['options']['num_ctx'] = 32768


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['endpoint', 'transport', 'model'])
async def test_original_final_show_response_mutation(installation, mutation):
    client, _, calls, _, _ = installation
    handler = client.transport.handler
    shows, hosts = [], []
    def response(request):
        hosts.append(request.url.host)
        result = handler(request)
        if request.url.path == '/api/show':
            shows.append(request)
            if len(shows) == 3:
                mutate(client, mutation)
        return result
    client.transport = httpx.MockTransport(response)
    async with client.generation_token_session() as counter:
        await client.preflight_generation('system', {}, {})
        with pytest.raises(ModelUnavailable, match='verified macOS Ollama') as error:
            await client.generate('system', {}, {'type': 'object'})
    assert len(shows) == 3
    assert set(hosts) == {'127.0.0.1'}
    assert all(path != '/api/generate' for path, _ in calls)
    assert 'private-source' not in str(error.value)
    assert client._request_binding.get() is None
    reaped(counter)


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['/api/version', '/api/tags', '/api/show'])
@pytest.mark.parametrize('mutation', ['endpoint', 'transport'])
async def test_mutation_before_each_metadata_await(installation, monkeypatch, endpoint, mutation):
    client, _, calls, _, _ = installation
    read = client._read_json
    async def before(http, method, path, **kwargs):
        if path == endpoint:
            await asyncio.sleep(0)
            mutate(client, mutation)
        return await read(http, method, path, **kwargs)
    monkeypatch.setattr(client, '_read_json', before)
    with pytest.raises(ModelUnavailable, match='verified macOS Ollama'):
        await client.preflight_generation('system', {}, {})
    order = ['/api/version', '/api/tags', '/api/show']
    assert [path for path, _ in calls] == order[:order.index(endpoint)]
    assert client._request_binding.get() is None
    assert client.generation_token_counter is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['endpoint', 'transport', 'model', 'body', 'context'])
async def test_mutation_in_awaited_final_post_handoff(installation, monkeypatch, mutation):
    client, _, calls, _, _ = installation
    post = client._post
    async def handoff(endpoint, body, **kwargs):
        await asyncio.sleep(0)
        mutate(client, mutation, body)
        return await post(endpoint, body, **kwargs)
    monkeypatch.setattr(client, '_post', handoff)
    async with client.generation_token_session() as counter:
        with pytest.raises(ModelUnavailable, match='verified macOS Ollama') as error:
            await client.generate('system', {}, {})
    assert 'private-source' not in str(error.value)
    assert all(path != '/api/generate' for path, _ in calls)
    reaped(counter)
    assert client._request_binding.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['endpoint', 'transport'])
@pytest.mark.parametrize('restore', [False, True])
async def test_actual_send_uses_constructed_url_and_transport(installation, monkeypatch, mutation, restore):
    client, _, calls, _, _ = installation
    send = httpx.AsyncClient.send
    original_endpoint, original_transport = client.base_url, client.transport
    expected = client.generation_request('system', {'exact': '雪'}, {'type': 'object'})
    hosts = []
    async def delayed(http, request, **kwargs):
        if request.url.path == '/api/generate':
            await asyncio.sleep(0)
            mutate(client, mutation)
            hosts.append(request.url.host)
            assert json.loads(request.content) == expected
            result = await send(http, request, **kwargs)
            if restore:
                client.base_url, client.transport = original_endpoint, original_transport
            return result
        return await send(http, request, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, 'send', delayed)
    async with client.generation_token_session() as counter:
        if restore:
            assert await client.generate('system', {'exact': '雪'}, {'type': 'object'}) == {}
        else:
            with pytest.raises(ModelUnavailable, match='verified macOS Ollama'):
                await client.generate('system', {'exact': '雪'}, {'type': 'object'})
    assert hosts == ['127.0.0.1']
    assert [body for path, body in calls if path == '/api/generate'] == [expected]
    reaped(counter)


@pytest.mark.asyncio
async def test_cancelled_handoff_resets_binding_and_reaps(installation, monkeypatch):
    client, _, calls, _, _ = installation
    entered = asyncio.Event()
    counters = []
    async def handoff(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(client, '_post', handoff)
    async def work():
        try:
            async with client.generation_token_session() as counter:
                counters.append(counter)
                await client.generate('system', {}, {})
        finally:
            assert client._request_binding.get() is None
            assert client.generation_token_counter is None
    task = asyncio.create_task(work())
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    reaped(counters[0])
    assert all(path != '/api/generate' for path, _ in calls)


@pytest.mark.asyncio
async def test_concurrent_and_nested_generations_keep_their_own_binding(monkeypatch):
    bodies = []
    async def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'response': '{}'})
    client = LocalModels(base_url='http://127.0.0.1:11434', preserve_content=True,
                         transport=guarded_transport(handler))
    post = client._post
    bindings = []
    entered = asyncio.Event()
    async def handoff(endpoint, body, **kwargs):
        binding = client._request_binding.get()
        bindings.append(binding)
        if 'outer' in body['system']:
            await client.generate('inner', {}, {})
            assert client._request_binding.get() is binding
        if 'first' in body['system']:
            entered.set()
            await asyncio.sleep(.01)
        if 'second' in body['system']:
            await entered.wait()
        return await post(endpoint, body, **kwargs)
    monkeypatch.setattr(client, '_post', handoff)
    await asyncio.gather(client.generate('first', {}, {}), client.generate('second', {}, {}))
    await client.generate('outer', {}, {})
    assert len(bodies) == 4
    assert len({id(binding) for binding in bindings}) == 4
    assert client._request_binding.get() is None


@pytest.mark.asyncio
async def test_injected_counter_cannot_bypass_frozen_routing(monkeypatch):
    from pipeline.token_budget import GenerationTokenCount, GenerationTokenIdentity
    import hashlib
    class Counter:
        identity = GenerationTokenIdentity('mistral:7b', 'a' * 64, 'b' * 64, 'c' * 64, '0.34.4')
        async def count(self, request):
            return GenerationTokenCount(self.identity, hashlib.sha256(request).hexdigest(), 10)
    client = LocalModels(base_url='http://127.0.0.1:11434', chat_model='mistral:7b',
                         preserve_content=True, generation_token_counter=Counter(),
                         transport=guarded_transport(lambda request: pytest.fail('generation sent')))
    post = client._post
    async def handoff(endpoint, body, **kwargs):
        mutate(client, 'endpoint')
        await asyncio.sleep(0)
        return await post(endpoint, body, **kwargs)
    monkeypatch.setattr(client, '_post', handoff)
    with pytest.raises(ModelUnavailable, match='verified macOS Ollama'):
        await client.generate('system', {}, {})
    assert client._request_binding.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['/api/version', '/api/tags', '/api/show'])
@pytest.mark.parametrize('mutation', ['endpoint', 'transport'])
async def test_metadata_send_is_frozen_even_if_mutation_is_restored(installation, monkeypatch, endpoint, mutation):
    client, _, calls, _, _ = installation
    send = httpx.AsyncClient.send
    original_endpoint, original_transport = client.base_url, client.transport
    urls = []
    async def delayed(http, request, **kwargs):
        urls.append(str(request.url))
        if request.url.path == endpoint:
            await asyncio.sleep(0)
            mutate(client, mutation)
            result = await send(http, request, **kwargs)
            client.base_url, client.transport = original_endpoint, original_transport
            return result
        return await send(http, request, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, 'send', delayed)
    async with client.generation_token_session() as counter:
        await counter.verify_provider()
    assert urls == [original_endpoint + path for path in ['/api/version', '/api/tags', '/api/show']]
    assert len(calls) == 3
    assert counter.runtime is None
    assert client._request_binding.get() is None


@pytest.mark.asyncio
async def test_final_handoff_rejects_changed_proof_identity(installation, monkeypatch):
    from dataclasses import replace
    client, _, calls, _, _ = installation
    post = client._post
    async def handoff(endpoint, body, **kwargs):
        counter = client.generation_token_counter
        await asyncio.sleep(0)
        counter.identity = replace(counter.identity, renderer_digest='0' * 64)
        return await post(endpoint, body, **kwargs)
    monkeypatch.setattr(client, '_post', handoff)
    async with client.generation_token_session() as counter:
        with pytest.raises(ModelUnavailable, match='complete request identity'):
            await client.generate('system', {}, {})
    assert all(path != '/api/generate' for path, _ in calls)
    reaped(counter)


@pytest.mark.asyncio
async def test_normal_routing_methods_counts_and_complete_body_are_unchanged(installation):
    client, _, calls, _, _ = installation
    handler = client.transport.handler
    requests = []
    def record(request):
        requests.append((request.method, str(request.url)))
        return handler(request)
    client.transport = httpx.MockTransport(record)
    expected = client.generation_request('system', {'exact': '雪'}, {'type': 'object'})
    async with client.generation_token_session() as counter:
        assert await client.preflight_generation('system', {'exact': '雪'}, {'type': 'object'})
        assert await client.generate('system', {'exact': '雪'}, {'type': 'object'}) == {}
    paths = ['version', 'tags', 'show', 'version', 'show', 'version', 'tags', 'show', 'generate']
    assert requests == [('GET' if path in {'version', 'tags'} else 'POST',
                         'http://127.0.0.1:11434/api/' + path) for path in paths]
    assert [body for path, body in calls if path == '/api/generate'] == [expected]
    reaped(counter)
