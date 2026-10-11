"""Pinned same-host Ollama 0.34.4 binding; no inference or private runner API.

Approval constants are reviewed release identities, never computed from whatever
happens to be installed. The runtime hashes the large model once in its owned
child. This module reads only bounded manifests and checks stat revisions.
"""
from collections import OrderedDict
from dataclasses import dataclass
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

import httpx

from pipeline.model_transport import model_json
from pipeline.token_budget import GenerationTokenCount, GenerationTokenIdentity
from .closure import LoaderMember
from .runtime import Asset, VocabularyRuntime, SOURCE_HASHES, _revision

BUNDLE = Path('/Applications/Ollama.app/Contents/Resources')
BACKEND = (
    ('libllama.0.4.1.dylib', 'dc17adf7beda178a5ef8db9437b78287136e0e31689416319284c2e78a9d621a'),
    ('libggml.0.24.0.dylib', '4b5e0f65f451ede9980c1f86b2416cc4e19200759b69db84c30d4d0925a319fc'),
    ('libggml-base.0.24.0.dylib', '2fb5beacef60b220c5c033bca7fc2d6c4cc5cb126b80d684c9ba73bf804f12e8'),
    ('ollama', 'bba8b79eac84ab092b9568978c1116e6c950eda588961b14d8608ac683a2af0b'),
)
LOADER_MEMBERS = (LoaderMember(0, 'libllama.0.dylib'),
                  LoaderMember(2, 'libggml.0.dylib'),
                  LoaderMember(3, 'libggml-base.0.dylib'))
TEMPLATE_SHA = '1ff5b64b61b9a63146475a24f70d3ca2fd6fdeec44247987163479968896fc0b'
# Exact manifests also pin config, template, parameters and absence of adapters,
# projectors, stored system/history, renderer/parser and remote routing layers.
MODELS = {
    'mistral:7b': ('6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf',
                   'f5074b1221da0f5a2910d33b642efa5b9eb58cfdddca1c79e16d7ad28aa2b31f'),
    'nomic-embed-text:latest': ('0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f',
                              '970aa74c0a90ef7482477cf803618e776e173c007bf957f635f1015bfcfef0e6'),
}


class CompatibilityError(RuntimeError):
    """Fixed actionable messages only; never expose provider/source content."""


def incompatible(part):
    return CompatibilityError('Native token counting requires the verified macOS Ollama 0.34.4 '
                              'installation and pinned Mistral 7b/Nomic assets; restore the reviewed '
                              'installation or select a supported profile (' + part + ').')


def canonical(model):
    return model + ':latest' if ':' not in model else model


def _small(asset):
    revision = _revision(asset.path)
    with open(asset.path, 'rb') as source:
        data = source.read(1024 * 1024 + 1)
    if (len(data) > 1024 * 1024 or _revision(asset.path) != revision
            or hashlib.sha256(data).hexdigest() != asset.sha256):
        raise incompatible('model metadata identity')
    return data


def discover(model):
    if sys.platform != 'darwin':
        raise incompatible('unsupported platform')
    name = canonical(model)
    if name not in MODELS:
        raise incompatible('unsupported model')
    manifest_sha, gguf_sha = MODELS[name]
    # Explicit configured store or the provider's standard same-user store.
    # Do not resolve aliases: the runtime rejects symlink components.
    root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models'))).absolute()
    base, tag = name.split(':')
    manifest = Asset(str(root / 'manifests/registry.ollama.ai/library' / base / tag), manifest_sha)
    data = json.loads(_small(manifest))
    library = Asset(str(BUNDLE / BACKEND[0][0]), BACKEND[0][1])
    model_asset = Asset(str(root / 'blobs' / ('sha256-' + gguf_sha)), gguf_sha)
    dependencies = [Asset(str(BUNDLE / file), digest) for file, digest in BACKEND[1:]]
    dependencies.append(manifest)
    for layer in [data['config'], *data['layers']]:
        digest = layer['digest'].removeprefix('sha256:')
        if digest == gguf_sha:
            continue
        asset = Asset(str(root / 'blobs' / ('sha256-' + digest)), digest)
        _small(asset)
        dependencies.append(asset)
    return library, model_asset, tuple(dependencies)


@dataclass(frozen=True)
class EmbeddingTokenCount:
    identity: GenerationTokenIdentity
    request_sha256: str
    frontend_tokens: tuple[int, ...]
    physical_tokens: tuple[int, ...]
    context_tokens: int = 2048
    dimensions: int = 768
    prefix_version: str = 'prefix-v1'

    @property
    def fits(self):
        return all(count <= self.context_tokens for count in self.physical_tokens)


class OllamaCounter:
    """One lazy bounded owner and exact proof LRU per physical plan.

    Constructed by LocalModels, not an application/QA injected adapter. Nomic
    counting accepts the *already prefixed* complete embedding request; it never
    changes document recipes, submits embeddings or performs refinement.
    """
    def __init__(self, client, *, deadline, embedding=False):
        self.client = client
        self.model = client.embedding_model if embedding else client.chat_model
        self.embedding = embedding
        self.deadline = deadline
        self.runtime = None
        self.identity = None
        self._cache = OrderedDict()
        self._lock = asyncio.Lock()
        self._closed = False
        self._endpoint = client.base_url

    async def verify_provider(self):
        if self._closed or asyncio.get_running_loop().time() >= self.deadline:
            raise incompatible('expired or closed plan')
        parsed = urlparse(self.client.base_url)
        if (self.client.base_url != self._endpoint or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
                or parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise incompatible('same-host loopback required')
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                     transport=self.client.transport, timeout=10) as http:
            version = await self.client._read_json(http, 'GET', '/api/version')
            tags = await self.client._read_json(http, 'GET', '/api/tags')
            show = await self.client._read_json(http, 'POST', '/api/show', body={'model': self.model})
        expected = MODELS[canonical(self.model)][0]
        matches = [row for row in tags.get('models', []) if canonical(row.get('name', '')) == canonical(self.model)]
        if version.get('version') != '0.34.4':
            raise incompatible('provider version')
        if len(matches) != 1 or matches[0].get('digest', '').removeprefix('sha256:') != expected:
            raise incompatible('provider model revision')
        if (show.get('details', {}).get('format') != 'gguf' or show.get('messages', []) != []
                or any(show.get(key) not in (None, '') for key in ('remote_host', 'remote_model', 'renderer', 'parser', 'system'))):
            raise incompatible('provider model backend')
        capability = 'embedding' if self.embedding else 'completion'
        if capability not in show.get('capabilities', []):
            raise incompatible('provider model capability')
        if self.embedding:
            info = show.get('model_info', {})
            if (info.get('general.architecture') != 'nomic-bert'
                    or info.get('nomic-bert.context_length') != 2048
                    or info.get('nomic-bert.embedding_length') != 768):
                raise incompatible('embedding context and dimensions')
        if not self.embedding and hashlib.sha256(show.get('template', '').encode()).hexdigest() != TEMPLATE_SHA:
            raise incompatible('template identity')
        if self._closed or asyncio.get_running_loop().time() >= self.deadline:
            raise incompatible('expired or closed plan')
        if self.runtime is not None:
            if self.runtime._closed:
                raise incompatible('expired or closed vocabulary')
            self.runtime._check_assets()

    async def _open(self):
        expected_name = 'nomic-embed-text:latest' if self.embedding else 'mistral:7b'
        if canonical(self.model) != expected_name:
            raise incompatible('unsupported model')
        assets = discover(self.model)
        await self.verify_provider()
        self.runtime = VocabularyRuntime(assets[0], assets[1], dependencies=assets[2],
                                        deadline=self.deadline, loader_members=LOADER_MEMBERS)
        await self.runtime.__aenter__()
        runtime_digest = hashlib.sha256(model_json({
            'assets': [(a.path, a.sha256) for a in (assets[0], assets[1], *assets[2])],
            'sources': SOURCE_HASHES, 'cpu': self.runtime.cpu,
            'flags': [True, True], 'embedding_frontend_flags': [False, True],
            'loader': [(m.asset_index, m.name) for m in LOADER_MEMBERS],
        }).encode()).hexdigest()
        self.identity = GenerationTokenIdentity(self.model, MODELS[canonical(self.model)][0],
            runtime_digest, TEMPLATE_SHA if not self.embedding else hashlib.sha256(b'prefix-v1').hexdigest(), '0.34.4')

    def _body(self, request):
        from pipeline.models import _unique_json_object
        if type(request) is not bytes or len(request) > 1024 * 1024:
            raise incompatible('request byte limit')
        body = json.loads(request, object_pairs_hook=_unique_json_object)
        if model_json(body).encode() != request or body.get('model') != self.model:
            raise incompatible('complete request identity')
        if self.embedding:
            if (set(body) != {'model', 'input', 'truncate'} or body['truncate'] is not False
                    or not isinstance(body['input'], list) or not 1 <= len(body['input']) <= 16
                    or any(not isinstance(text, str) or not text.startswith(('search_document: ', 'search_query: '))
                           for text in body['input'])):
                raise incompatible('embedding request contract')
        elif (set(body) != {'model', 'stream', 'shift', 'truncate', 'system', 'prompt', 'format', 'options'}
                or body['stream'] is not False or body['shift'] is not False or body['truncate'] is not True
                or model_json(body['options']) != model_json({'temperature': 0, 'num_ctx': 8192, 'num_predict': 1200})
                or not isinstance(body['system'], str) or not body['system']
                or not isinstance(body['prompt'], str) or not isinstance(body['format'], dict)):
            raise incompatible('generation request contract')
        return body

    async def count(self, request_bytes):
        try:
            async with asyncio.timeout_at(self.deadline), self._lock:
                if self._closed:
                    raise incompatible('closed plan')
                body = self._body(request_bytes)
                if self.runtime is None:
                    await self._open()
                self.runtime._check_assets()  # including on every cache hit
                if asyncio.get_running_loop().time() >= self.deadline:
                    raise TimeoutError
                key = (self.identity, hashlib.sha256(request_bytes).hexdigest())
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key]
                if self.embedding:
                    front, physical = [], []
                    for text in body['input']:
                        front.append(await self.runtime.count(text.encode(), add_special=False, parse_special=True))
                        physical.append(await self.runtime.count(text.encode(), add_special=True, parse_special=True))
                    proof = EmbeddingTokenCount(self.identity, key[1], tuple(front), tuple(physical))
                else:
                    # Verified legacy no-history render. Grammar is request-bound
                    # but never appended to input. [INST] is not a leading BOS,
                    # so completionPrompt's leading-BOS removal is a no-op.
                    rendered = ('[INST] ' + body['system'] + '\n\n' + body['prompt'] + '[/INST]').encode()
                    count = await self.runtime.count(rendered, add_special=True, parse_special=True)
                    proof = GenerationTokenCount(self.identity, key[1], count)
                self._cache[key] = proof
                if len(self._cache) > 512:
                    self._cache.popitem(last=False)
                return proof
        except BaseException as error:
            await self.aclose()
            if isinstance(error, (CompatibilityError, asyncio.CancelledError, TimeoutError)):
                raise
            raise incompatible('asset/runtime verification failed') from None

    async def aclose(self):
        self._closed = True
        self._cache.clear()
        if self.runtime is not None:
            await self.runtime.aclose()
