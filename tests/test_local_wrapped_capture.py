"""Wrapped delivery must outlive a short child and retry only explicit contention."""
import json
import asyncio
import sys
import threading
import time

import httpx
import pytest
from typer.testing import CliRunner

from logchat.local.cli import app
from logchat.local.client import LocalEmitter


def test_emitter_busy_retry_preserves_payload_and_model_rejection(monkeypatch):
    monkeypatch.setattr('logchat.local.client.binding', lambda project: {
        'source_ref': 'private', 'project_id': 'p', 'source_id': 's', 'api_url': 'http://127.0.0.1:18940'})
    monkeypatch.setattr('logchat.local.client.read_credential', lambda *args, **kwargs: 'synthetic-token')
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={'X-Logchat-Processing-Category': 'intake_busy_retry_required'})
        return httpx.Response(200, json={'accepted': 2})
    client_class = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(respond)))
    events = [{'event_id': 'one', 'message': 'synthetic'}, {'event_id': 'two', 'message': 'other'}]
    with LocalEmitter('.') as emitter:
        assert emitter.emit_batch(events, retry_busy=True) == {'accepted': 2}
    assert [json.loads(r.content) for r in calls] == [{'source_id': 's', 'events': events}] * 2
    assert all(300 < r.extensions['timeout']['read'] <= 310 for r in calls)
    calls.clear()
    def reject(request):
        calls.append(request)
        return httpx.Response(503, headers={'X-Logchat-Processing-Category': 'compact_invalid_output'})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(reject)))
    with LocalEmitter('.') as emitter, pytest.raises(httpx.HTTPStatusError):
        emitter.emit_batch(events, retry_busy=True)
    assert len(calls) == 1


def test_wrapped_exit_waits_for_acknowledgement_and_keeps_both_streams(tmp_path, monkeypatch):
    (tmp_path / 'rag.json').write_text('{"content_policy":"local_model_compact"}')
    config = {
        'local_state_dir': str(tmp_path), 'api_url': 'http://127.0.0.1:18940',
        'project_id': 'p', 'source_id': 's', 'source_ref': 'private'}
    monkeypatch.setattr('logchat.local.cli.attach_project', lambda *args: config)
    monkeypatch.setattr('logchat.local.client.binding', lambda project: config)
    monkeypatch.setattr('logchat.local.client.read_credential', lambda *args, **kwargs: 'synthetic-token')
    accepted = []
    async def acknowledge(request):
        # Exercise actual cancellable delivery beyond the former join deadline.
        if not accepted: await asyncio.sleep(8.2)
        events = json.loads(request.content)['events']
        accepted.extend(events)
        return httpx.Response(200, json={'accepted': len(events)})
    async_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: async_class(
        **kwargs, transport=httpx.MockTransport(acknowledge)))
    command = "import sys; print('stdout payment', flush=True); print('stderr payment', file=sys.stderr, flush=True); sys.exit(7)"
    result = CliRunner().invoke(app, ['run', '--project', str(tmp_path), '--', sys.executable, '-c', command])
    assert result.exit_code == 7
    assert {e['message'] for e in accepted} == {'stdout payment', 'stderr payment'}
    assert next(e for e in accepted if e['message'] == 'stderr payment')['level'] == 'error'
    assert 'stdout payment' in result.output and 'stderr payment' in result.output
    assert 'Coverage is incomplete' not in result.output


@pytest.mark.parametrize('failure', ['busy', 'outage'])
def test_wrapped_shutdown_has_one_drain_budget_and_closes_sender(tmp_path, monkeypatch, failure):
    config = {'local_state_dir': str(tmp_path), 'api_url': 'http://127.0.0.1:18940',
              'project_id': 'p', 'source_id': 's', 'source_ref': 'private'}
    monkeypatch.setattr('logchat.local.cli.attach_project', lambda *args: config)
    monkeypatch.setattr('logchat.local.client.binding', lambda project: config)
    monkeypatch.setattr('logchat.local.client.read_credential', lambda *args, **kwargs: 'synthetic-token')
    monkeypatch.setattr('logchat.local.cli.WRAPPED_DRAIN_SECONDS', 1.2)
    monkeypatch.setattr('logchat.local.client.EMITTER_INTAKE_SECONDS', 1.2)
    requests = []
    clients = []
    async def reject(request):
        requests.append(json.loads(request.content))
        if failure == 'outage':
            await asyncio.sleep(10)
        return httpx.Response(503, headers={'X-Logchat-Processing-Category': 'intake_busy_retry_required'})
    sync_class, async_class = httpx.Client, httpx.AsyncClient
    def sync_client(**kwargs):
        client = sync_class(**kwargs)
        clients.append(client)
        return client
    def async_client(**kwargs):
        client = async_class(**kwargs, transport=httpx.MockTransport(reject))
        clients.append(client)
        return client
    monkeypatch.setattr(httpx, 'Client', sync_client)
    monkeypatch.setattr(httpx, 'AsyncClient', async_client)
    # More than one delivery group: a per-group deadline would multiply shutdown.
    command = "import sys; [print('synthetic event '+str(i), flush=True) for i in range(401)]; sys.exit(7)"
    started = time.monotonic()
    result = CliRunner().invoke(app, ['run', '--project', str(tmp_path), '--', sys.executable, '-c', command])
    assert result.exit_code == 7
    assert time.monotonic()-started < 3
    assert '401 log events were not retained' in result.output
    assert 'Coverage is incomplete' in result.output
    assert 'transport_timeout' in result.output
    assert requests and len(requests) < 5
    batches = {}
    for value in requests:
        identifiers = tuple(event['event_id'] for event in value['events'])
        if identifiers in batches:
            assert value == batches[identifiers]
        batches[identifiers] = value
    assert clients and all(client.is_closed for client in clients)
    assert not any(thread.name == 'logchat-wrapped-sender' for thread in threading.enumerate())
