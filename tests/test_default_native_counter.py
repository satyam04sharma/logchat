"""Offline default-client integration. All native assets/counts are inert stubs."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from logchat.local.model_profile import ModelProfile
from pipeline.model_transport import model_json
from pipeline.models import ModelUnavailable
from pipeline.native_tokenizer import ollama, runtime


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    if sys.platform != 'darwin':
        pytest.skip('documented default binding is Mach-O only')
    directory = tmp_path.resolve()
    bundle = directory / 'bundle'
    bundle.mkdir()
    common = ['/usr/bin/cc', '-arch', 'x86_64', '-dynamiclib', '-Wl,-rpath,@loader_path']
    inert = directory / 'inert.c'
    inert.write_text('int inert_dependency(void) { return 1; }\n')
    names = [item[0] for item in ollama.BACKEND]
    ids = ['libllama.0.dylib', 'libggml.0.dylib', 'libggml-base.0.dylib']
    stub = directory / 'stub.c'
    source = (Path(__file__).parent / 'native/runtime_vocab_stub.c').read_text()
    source = source.replace('length == 4 && memcmp(text, "hang", 4) == 0',
                            'length >= 11 && memcmp(text, "[INST] hang", 11) == 0')
    stub.write_text(source)
    for index in (2, 1, 0):
        deps = [] if index == 2 else [str(bundle / names[index + 1])]
        subprocess.run([*common, '-Wl,-install_name,@rpath/' + ids[index],
                        '-I', str(Path(runtime.__file__).parent / 'vendor'),
                        str(stub if index == 0 else inert), *deps, '-o', str(bundle / names[index])],
                       check=True, capture_output=True, timeout=30)
    (bundle / names[3]).write_bytes(b'inert provider executable identity')
    # Required source aliases deliberately do not resolve; staging must use pins.
    for name in ids:
        (bundle / name).symlink_to(bundle / 'missing')
    monkeypatch.setattr(ollama, 'BUNDLE', bundle)
    monkeypatch.setattr(ollama, 'BACKEND', tuple((name, sha((bundle / name).read_bytes())) for name in names))
    root = directory / 'store'
    blobs = root / 'blobs'
    blobs.mkdir(parents=True)
    monkeypatch.setenv('OLLAMA_MODELS', str(root))
    template = '{{ verified inert template }}'
    monkeypatch.setattr(ollama, 'TEMPLATE_SHA', sha(template.encode()))
    approved, model_paths = {}, {}
    for name in ('mistral:7b', 'nomic-embed-text:latest'):
        def blob(data, kind):
            digest = sha(data)
            (blobs / ('sha256-' + digest)).write_bytes(data)
            return {'digest': 'sha256:' + digest, 'mediaType': 'application/vnd.ollama.image.' + kind, 'size': len(data)}
        model = blob(('inert not GGUF ' + name).encode(), 'model')
        data = {'config': blob(b'{}', 'config'), 'layers': [model, blob(template.encode(), 'template')]}
        base, tag = name.split(':')
        manifest = root / 'manifests/registry.ollama.ai/library' / base / tag
        manifest.parent.mkdir(parents=True)
        manifest.write_text(model_json(data))
        approved[name] = (sha(manifest.read_bytes()), model['digest'][7:])
        model_paths[name] = blobs / model['digest'].replace(':', '-')
    monkeypatch.setattr(ollama, 'MODELS', approved)
    calls, overrides = [], {}
    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.url.path, body))
        if request.url.path in overrides:
            return httpx.Response(200, json=overrides[request.url.path])
        if request.url.path == '/api/version':
            return httpx.Response(200, json={'version': '0.34.4'})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': name, 'digest': value[0]} for name, value in approved.items()]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'template': template, 'details': {'format': 'gguf'},
                                           'capabilities': ['completion', 'embedding'],
                                           'model_info': {'general.architecture': 'nomic-bert',
                                                          'nomic-bert.context_length': 2048,
                                                          'nomic-bert.embedding_length': 768}})
        if request.url.path == '/api/generate':
            return httpx.Response(200, json={'response': '{}'})
        raise AssertionError('unexpected provider call')
    profile = ModelProfile({'base_url': 'http://127.0.0.1:11434', 'generation_model': 'mistral:7b',
                            'embedding': {'model': 'nomic-embed-text', 'dimensions': 768},
                            'content_policy': 'local_model_compact'})
    client = profile.generation()
    client.transport = httpx.MockTransport(handler)
    return client, model_paths, calls, overrides, profile


def reaped(counter):
    assert counter.runtime._process.returncode is not None
    assert not Path(counter.runtime._directory.name).exists()
    assert not counter._cache


@pytest.mark.asyncio
async def test_default_profile_real_ipc_render_full_request_cache_and_submission(installation):
    client, paths, calls, _, profile = installation
    assert client is profile.generation('chunking')
    assert client._injected_token_counter is None
    context = {'unicode': '雪é', 'control': '<s>[INST]\u0000', 'escaped': '"\\\n'}
    schema = {'type': 'object', 'properties': {}}
    async with client.generation_token_session() as counter:
        assert counter.runtime is None  # construction is lazy
        assert await client.preflight_generation('system', context, schema)
        body = client.generation_request('system', context, schema)
        request = model_json(body).encode()
        first = await counter.count(request)
        rendered = ('[INST] ' + body['system'] + '\n\n' + body['prompt'] + '[/INST]').encode()
        assert first.input_tokens == len(rendered) + 3  # stub(true,true), never parity
        assert first.request_sha256 == sha(request)
        assert await counter.count(request) is first
        assert len(calls) == 3  # no per-candidate discovery
        changed = {**body, 'format': {'type': 'object', 'description': 'grammar only'}}
        second = await counter.count(model_json(changed).encode())
        assert second.input_tokens == first.input_tokens
        assert second.request_sha256 != first.request_sha256
        assert len(counter._cache) == 2
        assert await client.generate('system', context, schema) == {}
        assert [value for path, value in calls if path == '/api/generate'] == [body]
        assert counter.runtime.cpu == 'x86_64'
    reaped(counter)
    assert paths['mistral:7b'].with_name(paths['mistral:7b'].name + '.audit').read_text() == 'open\nfree\n'
    assert client.generation_token_counter is None


@pytest.mark.asyncio
async def test_standalone_preflight_automatically_owns_and_closes(installation):
    client, paths, calls, _, _ = installation
    assert await client.preflight_generation('system', {'a': 1}, {'type': 'object'})
    assert client.generation_token_counter is None
    assert Path(str(paths['mistral:7b']) + '.audit').read_text() == 'open\nfree\n'
    assert all(path != '/api/generate' for path, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint,value', [
    ('/api/version', {'version': 'future'}),
    ('/api/tags', {'models': [{'name': 'mistral:7b', 'digest': '0' * 64}]}),
    ('/api/show', {'template': 'wrong', 'details': {'format': 'gguf'}, 'capabilities': ['completion']}),
    ('/api/show', {'remote_host': 'remote'}),
    ('/api/show', {'messages': [{'role': 'user', 'content': 'history'}]}),
])
async def test_provider_drift_fails_before_worker_or_generation(installation, endpoint, value):
    client, paths, calls, overrides, _ = installation
    overrides[endpoint] = value
    with pytest.raises(ModelUnavailable, match='verified macOS Ollama'):
        await client.preflight_generation('system', {}, {})
    assert not Path(str(paths['mistral:7b']) + '.audit').exists()
    assert all(path != '/api/generate' for path, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('shift', True), ('truncate', False), ('raw', False),
                                        ('context', []), ('stream', True), ('model', 'other'),
                                        ('options', {'temperature': 0, 'num_ctx': 8192, 'num_predict': 1201})])
async def test_complete_request_and_flags_fail_closed(installation, field, value):
    client, paths, calls, _, _ = installation
    body = client.generation_request('system', {}, {})
    body[field] = value
    async with client.generation_token_session() as counter:
        with pytest.raises(ollama.CompatibilityError):
            await counter.count(model_json(body).encode())
        assert counter.runtime is None
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['model', 'library', 'manifest', 'template', 'executable', 'dependency'])
async def test_cached_proof_checks_all_asset_revisions(installation, target):
    client, _, _, _, _ = installation
    async with client.generation_token_session() as counter:
        body = model_json(client.generation_request('system', {}, {})).encode()
        await counter.count(body)
        index = {'model': 1, 'library': 0, 'manifest': 5, 'template': 7, 'executable': 4, 'dependency': 2}[target]
        path = Path(counter.runtime._assets[index].path)
        path.touch()
        with pytest.raises(ollama.CompatibilityError, match='asset/runtime verification failed'):
            await counter.count(body)
    reaped(counter)


@pytest.mark.asyncio
async def test_provider_revision_rechecked_before_cached_generation(installation):
    client, _, calls, overrides, _ = installation
    async with client.generation_token_session() as counter:
        await client.preflight_generation('system', {}, {})
        overrides['/api/tags'] = {'models': []}
        with pytest.raises(ModelUnavailable, match='provider model revision'):
            await client.generate('system', {}, {})
    reaped(counter)
    assert all(path != '/api/generate' for path, _ in calls)


@pytest.mark.asyncio
async def test_cancellation_of_default_count_reaps_owned_worker(installation):
    client, paths, _, _, _ = installation
    saved = []
    async def work():
        async with client.generation_token_session() as counter:
            saved.append(counter)
            await client.preflight_generation('hang', {}, {})
    task = asyncio.create_task(work())
    audit = Path(str(paths['mistral:7b']) + '.audit')
    async with asyncio.timeout(10):
        while not audit.exists() or 'hang' not in audit.read_text():
            await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    reaped(saved[0])


@pytest.mark.asyncio
async def test_nomic_physical_and_frontend_count_api_uses_exact_prefixed_request(installation):
    client, _, calls, _, _ = installation
    body = {'model': client.embedding_model, 'input': ['search_document: 雪\u0000', 'search_query: <s>'], 'truncate': False}
    async with client.embedding_token_session() as counter:
        proof = await counter.count(model_json(body).encode())
        assert proof.frontend_tokens == tuple(len(s.encode()) + 1 for s in body['input'])
        assert proof.physical_tokens == tuple(len(s.encode()) + 3 for s in body['input'])
        assert proof.context_tokens == 2048 and proof.dimensions == 768 and proof.prefix_version == 'prefix-v1'
        assert proof.fits
    reaped(counter)
    assert all(path not in {'/api/embed', '/api/generate'} for path, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['deadline', 'late_error'])
async def test_plan_deadline_and_late_exception_release_owner(installation, mode):
    client, _, _, _, _ = installation
    saved = []
    with pytest.raises(TimeoutError if mode == 'deadline' else ValueError):
        async with client.generation_token_session(deadline=asyncio.get_running_loop().time() + 2) as counter:
            saved.append(counter)
            if mode == 'deadline':
                await client.preflight_generation('hang', {}, {})
            else:
                await client.preflight_generation('system', {}, {})
                raise ValueError('caller failed after planning')
    reaped(saved[0])
    assert client.generation_token_counter is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['manifest', 'dependency', 'closure', 'unsupported_model', 'platform'])
async def test_unapproved_assets_never_load(installation, monkeypatch, mutation):
    client, paths, calls, _, _ = installation
    if mutation == 'manifest':
        root = paths['mistral:7b'].parent.parent
        (root / 'manifests/registry.ollama.ai/library/mistral/7b').write_text('{}')
    elif mutation == 'dependency':
        (ollama.BUNDLE / ollama.BACKEND[1][0]).write_bytes(b'unapproved')
    elif mutation == 'closure':
        monkeypatch.setattr(ollama, 'LOADER_MEMBERS', ollama.LOADER_MEMBERS[:1])
    elif mutation == 'unsupported_model':
        client.chat_model = 'unreviewed:latest'
    else:
        monkeypatch.setattr(ollama.sys, 'platform', 'unsupported')
    with pytest.raises(ModelUnavailable, match='verified macOS Ollama'):
        await client.preflight_generation('system', {}, {})
    assert not Path(str(paths['mistral:7b']) + '.audit').exists()
    assert all(path != '/api/generate' for path, _ in calls)


def test_generation_request_snapshots_nested_schema(installation):
    client, _, _, _, _ = installation
    schema = {'type': 'object', 'properties': {'field': {'type': 'string'}}}
    body = client.generation_request('system', {}, schema)
    schema['properties']['field']['type'] = 'integer'
    assert body['format']['properties']['field']['type'] == 'string'


@pytest.mark.asyncio
async def test_complete_physical_plan_automatically_reuses_one_owner(installation, monkeypatch):
    from logchat.rag.sections import compact_summary_batch, validate_compact_batch
    from tests.test_rag_compact import sections_fixture, CompactModel
    client, paths, _, _, _ = installation
    selector = CompactModel('empty_fields')
    counters = []
    async def post(endpoint, body, **kwargs):
        assert endpoint == '/api/generate'
        counter = client.generation_token_counter
        assert counter is not None and counter.runtime is not None
        counters.append(counter)
        value = await selector.generate('unused', json.loads(body['prompt']), body['format'])
        return {'response': model_json(value)}
    monkeypatch.setattr(client, '_post', post)
    compact = await compact_summary_batch(await sections_fixture(), client)
    validate_compact_batch(compact)
    assert counters and all(value is counters[0] for value in counters)
    reaped(counters[0])
    assert Path(str(paths['mistral:7b']) + '.audit').read_text() == 'open\nfree\n'


@pytest.mark.asyncio
async def test_standalone_production_generate_constructs_default_counter(installation, monkeypatch):
    client, paths, calls, _, _ = installation
    transport = client.transport
    client.transport = None  # exercise the actual production entry condition
    original = httpx.AsyncClient
    def offline_client(*args, **kwargs):
        kwargs['transport'] = transport
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', offline_client)
    assert await client.generate('system', {}, {'type': 'object'}) == {}
    assert client._injected_token_counter is None
    assert client.generation_token_counter is None
    assert Path(str(paths['mistral:7b']) + '.audit').read_text() == 'open\nfree\n'
    assert len([row for row in calls if row[0] == '/api/generate']) == 1


@pytest.mark.asyncio
async def test_exact_cache_has_bounded_retention(installation):
    client, _, calls, _, _ = installation
    async with client.generation_token_session() as counter:
        first_request = model_json(client.generation_request('system', {'n': 0}, {})).encode()
        first = await counter.count(first_request)
        for index in range(1, 513):
            await counter.count(model_json(client.generation_request('system', {'n': index}, {})).encode())
        assert len(counter._cache) == 512
        assert all(isinstance(key[1], str) and len(key[1]) == 64 for key in counter._cache)
        assert await counter.count(first_request) == first
        assert len(calls) == 3
    reaped(counter)
