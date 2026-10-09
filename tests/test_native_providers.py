import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from types import SimpleNamespace

import pytest

from cli.secrets import write_credential, secret_dir
from connectors.provider_cli import ProviderError, bounded_run, command, fetch_window, normalize
from logchat.local.providers import ProviderCollection, validate_config
from logchat.local.store import LocalStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('LOGCHAT_SECRETS_DIR', str(tmp_path / 'credentials'))
    store = LocalStore(tmp_path / 'state')
    project = store.create_project('provider fixtures')
    source = store.create_source(project['id'], 'fixture', 'dev', 'railway', None)
    accepted = []
    async def ingest(project_id, source_id, events):
        accepted.extend(events)
        return {'accepted': len(events), 'state': 'durably_prepared'}
    store.rag_runtime = SimpleNamespace(model_profile=object(), ingest_async=ingest)
    manager = ProviderCollection(store)
    config = {'provider_project': 'remote-project', 'provider_environment': 'production', 'service': 'app',
              'since': (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(), 'interval_seconds': 1}
    return store, project, source, manager, config, accepted


def windows():
    since = datetime(2026, 10, 4, tzinfo=timezone.utc)
    return since, since + timedelta(minutes=5)


@pytest.mark.parametrize('kind', ['railway', 'vercel', 'cli'])
def test_preserved_fields_timestamp_stable_ids_and_identical_duplicates(kind):
    since, until = windows()
    row = {'timestamp': since.isoformat(), 'message': 'body-marker', 'requestId': 'same-request', 'account': {'email': 'a@example.test'}}
    blob = (json.dumps(row) + '\n') * 2
    events = normalize(kind, blob.encode(), 'source', since, until)
    assert len(events) == 2 and events[0].event_id != events[1].event_id
    assert events == normalize(kind, blob.encode(), 'source', since, until)
    assert events[0].ts == since and 'a@example.test' in events[0].message
    assert 'same-request' in events[0].message


def test_docker_timestamp_and_original_app_timestamp():
    since, until = windows()
    row = {'timestamp': '2026-01-01T00:00:00Z', 'msg': 'ok', 'nested': {'field': 42}}
    events = normalize('docker', (since.isoformat() + ' ' + json.dumps(row)).encode(), 's', since, until)
    assert events[0].ts == since and 'application_timestamp' in events[0].message and 'nested' in events[0].message


def test_milliseconds_and_half_open_window():
    since, until = windows()
    rows = [{'created': int(since.timestamp() * 1000), 'message': 'a'}, {'timestamp': until.isoformat(), 'message': 'b'}]
    events = normalize('vercel', '\n'.join(json.dumps(r) for r in rows).encode(), 's', since, until)
    assert len(events) == 1 and events[0].ts == since


@pytest.mark.parametrize('blob', [b'{broken', b'{"message":"missing timestamp"}', b'{"timestamp":"2026-10-04","message":"naive"}', b'[]', b'\xff'])
def test_malformed_windows_fail_closed(blob):
    with pytest.raises(ProviderError, match='provider_invalid_event'):
        normalize('cli', blob, 's', *windows())


def test_capped_window_fails_without_returning_partial_events():
    since, until = windows()
    row = json.dumps({'timestamp': since.isoformat(), 'message': 'ok'}) + '\n'
    with pytest.raises(ProviderError, match='provider_window_capped'):
        normalize('railway', (row * 401).encode(), 's', since, until)


def test_subprocess_bound_during_capture_timeout_and_rate_limit():
    with pytest.raises(ProviderError, match='provider_window_capped'):
        bounded_run([sys.executable, '-c', 'import sys;sys.stdout.write("x"*1000000)'], max_bytes=1000)
    with pytest.raises(ProviderError, match='provider_timeout'):
        bounded_run([sys.executable, '-c', 'import time;time.sleep(1)'], timeout=.05)
    with pytest.raises(ProviderError, match='provider_rate_limited') as failure:
        bounded_run([sys.executable, '-c', 'import sys;sys.stderr.write("429 retry-after: 17 private-token");sys.exit(1)'])
    assert failure.value.retry_after == 17 and 'private-token' not in str(failure.value)


def test_explicit_commands_and_environment_credentials(monkeypatch):
    since, until = windows()
    config = {'provider_project': 'p', 'provider_environment': 'production', 'service': 's', 'scope': 'team'}
    argv = command('railway', config, since, until)
    assert '--project' in argv and '--environment' in argv and '--service' in argv and '--until' in argv
    argv = command('vercel', config, since, until)
    assert '--no-branch' in argv and '--scope' in argv and '--expand' in argv
    captured = []
    def run(argv, **kwargs):
        captured.append((argv, kwargs['env']))
        return b'', b''
    monkeypatch.setattr('connectors.provider_cli.bounded_run', run)
    monkeypatch.setenv('CUSTOM_TOKEN', 'hidden-token-value')
    fetch_window('vercel', {**config, 'token_env': 'CUSTOM_TOKEN'}, 's', since, until)
    assert captured[0][1]['VERCEL_TOKEN'] == 'hidden-token-value'
    assert 'hidden-token-value' not in json.dumps(captured[0][0])
    monkeypatch.setenv('RAILWAY_API_TOKEN', 'account-token')
    monkeypatch.setenv('RAILWAY_TOKEN', 'inherited-project-token')
    fetch_window('railway', {**config, 'token_env': 'CUSTOM_TOKEN'}, 's', since, until)
    assert captured[-1][1]['RAILWAY_TOKEN'] == 'hidden-token-value'
    assert 'RAILWAY_API_TOKEN' not in captured[-1][1]
    fetch_window('railway', {**config, 'token_env': 'RAILWAY_API_TOKEN'}, 's', since, until)
    assert captured[-1][1]['RAILWAY_API_TOKEN'] == 'account-token'
    assert 'RAILWAY_TOKEN' not in captured[-1][1]


@pytest.mark.parametrize('config', [
    {'argv': ['tool', '--token', 'secret', '{since}', '{until}']},
    {'argv': ['tool', '{since}']},
    {'argv': ['tool', '{since}', '{until}'], 'token_ref': '../../secret'},
])
def test_generic_argv_validation(config):
    with pytest.raises(ValueError):
        validate_config('cli', config)


def test_binding_and_model_are_required(setup):
    store, project, source, manager, config, _ = setup
    with pytest.raises(ValueError, match='invalid_provider_binding'):
        manager.configure('wrong-project', source['id'], 'railway', config)
    with pytest.raises(ValueError, match='invalid_provider_binding'):
        manager.configure(project['id'], source['id'], 'vercel', {k:v for k,v in config.items() if k != 'service'})
    store.rag_runtime = None
    with pytest.raises(ValueError, match='provider_model_not_configured'):
        manager.configure(project['id'], source['id'], 'railway', config)


@pytest.mark.asyncio
async def test_durable_cursor_resume_empty_observation_and_no_raw_metadata(setup, monkeypatch):
    store, project, source, manager, config, accepted = setup
    initial = manager.configure(project['id'], source['id'], 'railway', config)['cursor']
    def fetch(kind, config, sid, since, until, **kwargs):
        return normalize(kind, json.dumps({'timestamp': since.isoformat(), 'message': 'raw-body-sentinel'}).encode(), sid, since, until)
    monkeypatch.setattr('logchat.local.providers.fetch_window', fetch)
    result = (await manager.poll_once())[0]
    assert result['state'] == 'observed' and result['cursor'] != initial and result['complete_coverage'] is False
    assert len(accepted) == 1
    resumed = ProviderCollection(store)
    assert resumed.status()[0]['cursor'] == result['cursor']
    with store.connection(write=True) as connection:
        connection.execute('UPDATE native_provider_sources SET next_poll_at=0')
    monkeypatch.setattr('logchat.local.providers.fetch_window', lambda *a, **k: [])
    empty = (await resumed.poll_once())[0]
    assert empty['state'] == 'observed_empty' and empty['cursor'] != result['cursor']
    with store.connection() as connection:
        metadata = json.dumps([dict(r) for r in connection.execute('SELECT * FROM native_provider_sources')])
    assert 'raw-body-sentinel' not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt', [None, {'accepted': 1, 'state': 'queued'}, {'accepted': 0, 'state': 'durably_prepared'}])
async def test_failed_intake_never_advances(setup, monkeypatch, receipt):
    store, project, source, manager, config, _ = setup
    initial = manager.configure(project['id'], source['id'], 'railway', config)['cursor']
    def fetch(kind, config, sid, since, until, **kwargs):
        return normalize(kind, json.dumps({'timestamp': since.isoformat(), 'message': 'private-body'}).encode(), sid, since, until)
    monkeypatch.setattr('logchat.local.providers.fetch_window', fetch)
    async def ingest(*args):
        if receipt is None:
            raise RuntimeError('private-body private-token')
        return receipt
    store.rag_runtime.ingest_async = ingest
    result = (await manager.poll_once())[0]
    assert result['cursor'] == initial and result['state'] == 'failed'
    assert 'private-' not in json.dumps(result)


@pytest.mark.asyncio
async def test_caps_halve_same_window_rate_limit_persists(setup, monkeypatch):
    store, project, source, manager, config, _ = setup
    initial = manager.configure(project['id'], source['id'], 'railway', config)['cursor']
    def capped(*args, **kwargs):
        raise ProviderError('provider_window_capped')
    monkeypatch.setattr('logchat.local.providers.fetch_window', capped)
    result = (await manager.poll_once())[0]
    assert result['window_seconds'] == 150 and result['cursor'] == initial
    with store.connection(write=True) as connection:
        connection.execute('UPDATE native_provider_sources SET retry_at=0')
    def limited(*args, **kwargs):
        raise ProviderError('provider_rate_limited', 60)
    monkeypatch.setattr('logchat.local.providers.fetch_window', limited)
    result = (await manager.poll_once())[0]
    assert result['state'] == 'rate_limited' and result['cursor'] == initial
    assert (await ProviderCollection(store).poll_once())[0]['retry_at'] == result['retry_at']


@pytest.mark.asyncio
async def test_scoped_credential_private_status_reconnect_and_disable(setup, monkeypatch):
    store, project, source, manager, config, _ = setup
    reference = write_credential('provider-secret-sentinel', purpose='native_provider', project_id=project['id'], source_id=source['id'], kind='railway')
    manager.configure(project['id'], source['id'], 'railway', {**config, 'token_ref': reference})
    manager.configure(project['id'], source['id'], 'railway', config)
    tokens = []
    def fetch(*args, token=None, **kwargs):
        tokens.append(token)
        return []
    monkeypatch.setattr('logchat.local.providers.fetch_window', fetch)
    await manager.poll_once()
    assert tokens == ['provider-secret-sentinel']
    status = json.dumps(manager.status())
    assert reference not in status and 'provider-secret-sentinel' not in status and 'remote-project' not in status
    with store.connection() as connection:
        metadata = json.dumps([dict(r) for r in connection.execute('SELECT * FROM native_provider_sources')])
    assert 'provider-secret-sentinel' not in metadata
    manager.disable(project['id'], source['id'])
    assert not (secret_dir() / (reference + '.json')).exists()
    assert ProviderCollection(store).status()[0]['state'] == 'disabled'
    assert await manager.poll_once() == []


@pytest.mark.asyncio
async def test_wrong_credential_scope_fails_before_provider(setup, monkeypatch):
    store, project, source, manager, config, _ = setup
    reference = write_credential('secret', purpose='native_provider', project_id=project['id'], source_id='other', kind='railway')
    manager.configure(project['id'], source['id'], 'railway', {**config, 'token_ref': reference})
    monkeypatch.setattr('logchat.local.providers.fetch_window', lambda *a, **k: pytest.fail('must not read provider'))
    result = (await manager.poll_once())[0]
    assert result['error_code'] == 'provider_credential_unavailable' and result['cursor'] == config['since']


@pytest.mark.asyncio
async def test_corrupt_config_blocks_poll_but_can_be_disabled(setup, monkeypatch):
    store, project, source, manager, config, _ = setup
    manager.configure(project['id'], source['id'], 'railway', config)
    with store.connection(write=True) as connection:
        connection.execute("UPDATE native_provider_sources SET config_json='malformed'")
    monkeypatch.setattr('logchat.local.providers.fetch_window', lambda *a, **k: pytest.fail('must not read provider'))
    result = (await manager.poll_once())[0]
    assert result['error_code'] == 'invalid_provider_config'
    result = manager.disable(project['id'], source['id'])
    assert not result['enabled'] and result['state'] == 'disabled'
    assert await manager.poll_once() == []


def test_unsupported_platform_has_safe_code(monkeypatch):
    monkeypatch.setattr('connectors.provider_cli.os.name', 'nt')
    with pytest.raises(ProviderError, match='provider_platform_unsupported'):
        bounded_run(['unused'])


@pytest.mark.asyncio
async def test_native_raw_receipt_replay_survives_intake_cursor_crash_gap(setup, monkeypatch):
    from logchat.local.raw_capture import save_capture_policy
    store, project, source, manager, config, _ = setup
    save_capture_policy(store.state_dir, mode='retain_until_summarized')
    store.rag_runtime = None
    initial = manager.configure(project['id'], source['id'], 'railway', config)['cursor']
    def fetch(kind, config, sid, since, until, **kwargs):
        row = json.dumps({'timestamp': since.isoformat(), 'message': 'raw-replay-fixture'}) + '\n'
        return normalize(kind, (row * 2).encode(), sid, since, until)
    monkeypatch.setattr('logchat.local.providers.fetch_window', fetch)
    result = (await manager.poll_once())[0]
    assert result['state'] == 'observed' and result['observed_events'] == 2
    # Simulate durable intake and a process failure before cursor commit.
    with store.connection(write=True) as connection:
        connection.execute('UPDATE native_provider_sources SET cursor=?,next_poll_at=0,observed_events=0', (initial,))
    restarted_store = LocalStore(store.state_dir)
    replay = (await ProviderCollection(restarted_store).poll_once())[0]
    assert replay['cursor'] == result['cursor'] and replay['state'] == 'observed'
    statistics = restarted_store.rag_runtime.raw_capture.status(project['id'])
    assert statistics['pending_events'] == 2


@pytest.mark.asyncio
async def test_overlapping_managers_and_lifecycle(setup, monkeypatch):
    store, project, source, manager, config, _ = setup
    manager.configure(project['id'], source['id'], 'railway', config)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    async def thread_fetch(*args, **kwargs):
        calls.append(1)
        entered.set()
        await release.wait()
        return []
    # Exercise the asynchronous lease around a controlled worker substitute.
    real_thread = asyncio.to_thread
    async def controlled(function, *args, **kwargs):
        if function.__name__ == 'fetch_window':
            return await thread_fetch(*args, **kwargs)
        return await real_thread(function, *args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', controlled)
    await manager.start()
    await asyncio.wait_for(entered.wait(), 2)
    second = ProviderCollection(store)
    await second.poll_once()
    assert calls == [1]
    busy = await manager.poll_once()
    assert busy[0]['state'] == 'busy'
    release.set()
    await manager.stop()
    assert manager.task is None
    with store.connection() as connection:
        row = connection.execute('SELECT lease_owner,lease_until FROM native_provider_sources').fetchone()
    assert row['lease_owner'] is None and row['lease_until'] == 0


@pytest.mark.asyncio
async def test_model_processing_failure_reports_only_allowlisted_category_without_advancing(setup,monkeypatch):
    from pipeline.models import ModelUnavailable
    store,project,source,manager,config,_=setup
    initial=manager.configure(project['id'],source['id'],'railway',config)['cursor']
    def fetch(kind,config,sid,since,until,**kwargs):
        return normalize(kind,json.dumps({'timestamp':since.isoformat(),'message':'private-body'}).encode(),sid,since,until)
    monkeypatch.setattr('logchat.local.providers.fetch_window',fetch)
    async def ingest(*args):
        error=ModelUnavailable('private-body private-token')
        error.category='section_invalid_partition'
        raise error
    store.rag_runtime.ingest_async=ingest
    result=(await manager.poll_once())[0]
    assert result['cursor']==initial
    assert result['error_code']=='section_invalid_partition'
    assert 'private-' not in json.dumps(result)
