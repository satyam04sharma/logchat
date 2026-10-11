"""The schema sent and validated is one snapshot, independent of caller mutation.

All HTTP is controlled MockTransport traffic; no model or native assets are used.
"""
import asyncio
from copy import deepcopy
import json
import socket

import httpx
import pytest

from pipeline.models import LocalModels, ModelUnavailable


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('actual network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


@pytest.mark.asyncio
async def test_exact_original_type_mutation_reproduction():
    schema = {'type': 'object', 'properties': {'value': {'type': 'string'}},
              'required': ['value'], 'additionalProperties': False}
    posts = []

    def handler(request):
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'},
                                           'capabilities': ['completion'], 'messages': []})
        assert request.url.path == '/api/generate'
        posts.append(json.loads(request.content))
        assert posts[-1]['format']['properties']['value']['type'] == 'string'
        schema['properties']['value']['type'] = 'integer'
        return httpx.Response(200, json={'response': '{"value":7}', 'done_reason': 'stop'})

    models = LocalModels(base_url='http://127.0.0.1:11434', chat_model='mistral:7b',
                         transport=httpx.MockTransport(handler))
    with pytest.raises(ModelUnavailable, match='invalid structured output'):
        await models.generate('Controlled schema test.', {'message': 'controlled'}, schema)
    assert len(posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('preserve_content', [False, True])
@pytest.mark.parametrize('mutation_at', ['/api/version', '/api/show', '/api/generate'])
@pytest.mark.parametrize('case', ['unchanged', 'required', 'enum', 'additionalProperties',
                                  'maxLength', 'removed', 'tightened'])
async def test_nested_snapshot_survives_awaited_caller_mutation(
        monkeypatch, preserve_content, mutation_at, case):
    nested = {'type': 'object', 'properties': {
        'value': {'type': 'string', 'enum': ['ok', 'long'], 'maxLength': 2}},
        'required': ['value'], 'additionalProperties': False}
    schema = {'type': 'object', 'properties': {'nested': nested},
              'required': ['nested'], 'additionalProperties': False}
    original = deepcopy(schema)
    response = {'nested': {'value': 'ok'}}
    if case == 'required':
        response = {'nested': {}}
    elif case == 'enum':
        response['nested']['value'] = 'no'
    elif case == 'additionalProperties':
        response['nested']['extra'] = True
    elif case == 'maxLength':
        response['nested']['value'] = 'long'
    elif case == 'removed':
        response = {}
    expected_schema = None
    posts, bodies = [], []

    async def handler(request):
        nonlocal expected_schema
        await asyncio.sleep(0)
        if request.url.path == mutation_at:
            if case == 'required':
                nested['required'].clear()
            elif case == 'enum':
                nested['properties']['value']['enum'].append('no')
            elif case == 'additionalProperties':
                nested['additionalProperties'] = True
            elif case == 'maxLength':
                nested['properties']['value']['maxLength'] = 100
            elif case == 'removed':
                schema.clear()
            elif case == 'tightened':
                nested['required'].append('missing')
                nested['properties']['value']['enum'][:] = ['no']
                nested['properties']['value']['maxLength'] = 1
            expected_schema = deepcopy(schema)
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'details': {'format': 'gguf'},
                                           'capabilities': ['completion'], 'messages': []})
        assert request.url.path == '/api/generate'
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={'response': json.dumps(response), 'done_reason': 'stop'})

    models = LocalModels(base_url='http://127.0.0.1:11434', chat_model='mistral:7b',
                         preserve_content=preserve_content, transport=httpx.MockTransport(handler))
    generation_request = models.generation_request

    def capture(*args):
        body = generation_request(*args)
        bodies.append((body, deepcopy(body)))
        return body

    monkeypatch.setattr(models, 'generation_request', capture)
    if case in {'unchanged', 'tightened'}:
        assert await models.generate('Controlled test.', {}, schema) == response
    else:
        with pytest.raises(ModelUnavailable, match='invalid structured output'):
            await models.generate('Controlled test.', {}, schema)
    assert len(posts) == len(bodies) == 1
    body, before = bodies[0]
    assert posts[0] == body == before
    assert body['format'] == original
    assert schema == expected_schema
