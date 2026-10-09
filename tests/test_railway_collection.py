import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import threading
from unittest.mock import patch

import pytest

from connectors.base import ConnectorError
from connectors.railway_cli import RailwayCLIConnector, RailwayRateLimited
from logchat.local.collection import collect_once
from logchat.local.store import LocalStore
from connectors.local import parse_event


def test_empty_provider_messages_preserve_all_records_and_metrics():
    from connectors.railway_cli import EMPTY_MESSAGE_PLACEHOLDER

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    variants = [{}, {'message': None}, {'message': ''}, {'message': ' \t'},
                {'msg': None}, {'event': ''}, {'msg': 'real alias message'},
                {'message': 'real message'}]
    values = [{'timestamp': start.isoformat(), 'level': 'error', 'http_status': 503,
               'duration_ms': 230, **variant} for variant in variants]
    # Out-of-window normalization must not inflate committed diagnostics.
    values.append({'timestamp': (start+timedelta(hours=1)).isoformat(), 'message': None})
    connector = RailwayCLIConnector({'service': 'api'})
    result = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, values)).encode())
    with patch('connectors.railway_cli.subprocess.run', return_value=result):
        events = connector._read(start, start+timedelta(hours=1))
    assert len(events) == 8
    assert [event.message for event in events[:6]] == [EMPTY_MESSAGE_PLACEHOLDER]*6
    assert [event.message for event in events[6:]] == ['real alias message', 'real message']
    assert all(event.ts == start and event.level == 'error' and event.duration_ms == 230
               and event.request_status == 503 and event.service == 'api' for event in events)
    assert connector.message_normalization_counts == {'missing': 1, 'null': 2, 'empty': 3}


@pytest.mark.parametrize('invalid', [
    [], None, {'message': None}, {'timestamp': 'bad', 'message': None},
    {'timestamp': '2026-10-01T00:00:00Z', 'message': False},
    {'timestamp': '2026-10-01T00:00:00Z', 'message': 12},
    {'timestamp': '2026-10-01T00:00:00Z', 'msg': {}},
    {'timestamp': '2026-10-01T00:00:00Z', 'message': None, 'duration_ms': -1},
    {'timestamp': '2026-10-01T00:00:00Z', 'message': '', 'http_status': 999},
])
def test_empty_message_normalization_does_not_accept_invalid_records(invalid):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    connector = RailwayCLIConnector({'service': 'api'})
    # A valid normalized record before the malformed one cannot make this page succeed.
    values = [{'timestamp': start.isoformat(), 'message': None}, invalid]
    result = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, values)).encode())
    with patch('connectors.railway_cli.subprocess.run', return_value=result):
        with pytest.raises(ConnectorError, match='railway_cli_invalid_event'):
            connector._read(start, start+timedelta(hours=1))
    assert connector.message_normalization_counts == {'missing': 0, 'null': 0, 'empty': 0}


def test_normalized_provider_window_commits_exact_counts_and_safe_diagnostics(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    values = [{'timestamp': start.isoformat(), 'message': None, 'level': 'error',
               'duration_ms': index, 'http_status': 503} for index in range(6)]
    result = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, values)).encode())
    with patch('connectors.railway_cli.subprocess.run', return_value=result):
        outcome = asyncio.run(collect_once(tmp_path, config))[0]
    assert outcome['accepted'] == 6 and outcome['status'] == 'observed'
    assert outcome['message_normalization_counts'] == {'missing': 0, 'null': 6, 'empty': 0}
    rows = store.evidence(config['project_id'])
    assert sum(row['event_count'] for row in rows) == 6
    assert sum(row['duration_count'] for row in rows) == 6
    assert sum(row['duration_sum_ms'] for row in rows) == 15
    assert rows[0]['status_counts'] == {'503': 6}
    with store.connection() as connection:
        assert connection.execute('SELECT cursor FROM collection_cursors WHERE source_id=?',
                                  (source['id'],)).fetchone()[0] == outcome['cursor']


def test_normalized_blank_messages_do_not_fabricate_lexical_relevance(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    variants = [{}, {'message': None}, {'message': ''}, {'message': ' \t'},
                {'msg': None}, {'event': ''}]
    values = [{'timestamp': start.isoformat(), 'level': 'error', 'http_status': 503,
               'duration_ms': 230, **variant} for variant in variants]
    result = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, values)).encode())
    with patch('connectors.railway_cli.subprocess.run', return_value=result):
        outcome = asyncio.run(collect_once(tmp_path, config))[0]

    assert outcome['accepted'] == 6 and outcome['status'] == 'observed'
    assert outcome['message_normalization_counts'] == {'missing': 1, 'null': 2, 'empty': 3}
    project_id = config['project_id']
    for query in ('unavailable', 'timeout', 'Which unavailable errors were observed?',
                  'Which timeouts were observed?'):
        assert store.evidence(project_id, query) == []
    rows = store.evidence(project_id)
    assert len(rows) == 1
    blank = rows[0]
    assert 'Aggregate pattern: uncategorized application event.' in blank['summary']
    assert blank['level'] == 'error' and blank['bucket_start'] == start.isoformat()
    assert blank['event_count'] == blank['duration_count'] == 6
    assert blank['duration_sum_ms'] == 1380
    assert blank['duration_min_ms'] == blank['duration_max_ms'] == 230
    assert blank['status_counts'] == {'503': 6}

    # Genuine operational text in the same bucket still matches independently.
    store.ingest(project_id, source['id'], [parse_event({
        'timestamp': start.isoformat(), 'service': 'api', 'level': 'error',
        'message': message,
    }) for message in ('connection unavailable', 'request timeout')])
    for query in ('unavailable', 'timeout'):
        matches = store.evidence(project_id, query)
        assert len(matches) == 1 and matches[0]['event_count'] == 1
        assert matches[0]['id'] != blank['id']
    assert sum(row['event_count'] for row in store.evidence(project_id)) == 8


def test_cli_parses_demo_app_metrics_and_excludes_right_boundary():
    start=datetime(2026,10,1,tzinfo=timezone.utc);end=start+timedelta(hours=1)
    output='\n'.join(json.dumps({'timestamp':value.isoformat(),'message':'request timeout',
        'component':'http','http_status':503,'duration_ms':230,'level':'error'}) for value in (start,end))
    with patch('connectors.railway_cli.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=output.encode())) as command:
        events=RailwayCLIConnector({'service':'api','command_prefix':['democtl','prod','railway']})._read(start,end)
    assert len(events)==1 and events[0].request_status==503 and events[0].duration_ms==230
    assert events[0].service=='api'
    assert command.call_args.args[0][:3]==['democtl','prod','railway']
    assert command.call_args.kwargs['capture_output'] and command.call_args.kwargs['timeout']==45


def test_upstream_error_and_malformed_output_do_not_leak():
    connector=RailwayCLIConnector({'service':'api'})
    now=datetime.now(timezone.utc)
    for code,output,expected in [(1,b'token=private-value','railway_cli_failed'),(0,b'raw private log','railway_cli_invalid_event')]:
        with patch('connectors.railway_cli.subprocess.run',return_value=SimpleNamespace(returncode=code,stdout=output,stderr=b'')):
            with pytest.raises(ConnectorError,match=expected) as error:connector._read(now,now+timedelta(seconds=2))
            assert 'private' not in str(error.value)


def test_cap_is_split_and_no_truncated_page_is_accepted():
    start=datetime(2026,10,1,tzinfo=timezone.utc);end=start+timedelta(seconds=4)
    connector=RailwayCLIConnector({'service':'api'});calls=[]
    def read(left,right):
        calls.append((left,right))
        if right-left>timedelta(seconds=2):raise ConnectorError('railway_cli_window_capped')
        return [parse_event({'timestamp':left.isoformat(),'message':'request timeout'})]
    async def fetch():return [event async for event in connector.fetch(start,end)]
    with patch.object(connector,'_read',side_effect=read):events=asyncio.run(fetch())
    assert len(events)==2 and len(calls)==3


def test_checkpoint_survives_restart_and_replay_cannot_double_count(tmp_path):
    store=LocalStore(tmp_path);project=store.create_project('demo_app')
    source=store.create_source(project['id'],'api','dev','push',None)
    event=parse_event({'timestamp':'2026-09-01T10:00:00Z','message':'RAW-CANARY request timeout token=private-value','service':'api','level':'error'})
    store.ingest(project['id'],source['id'],[event],checkpoint=(None,'2026-09-01T11:00:00+00:00'))
    restarted=LocalStore(tmp_path)
    with pytest.raises(RuntimeError,match='cursor changed'):
        restarted.ingest(project['id'],source['id'],[event],checkpoint=(None,'2026-09-01T11:00:00+00:00'))
    rows=restarted.evidence(project['id'],'timeout')
    assert len(rows)==1 and rows[0]['event_count']==1
    assert rows[0]['bucket_start'].startswith('2026-09-01')  # Application memory has no host's seven-day expiry.
    assert b'RAW-CANARY' not in (tmp_path/'local.db').read_bytes()
    assert b'private-value' not in (tmp_path/'local.db').read_bytes()


def test_partial_provider_failure_does_not_commit_events_or_cursor(tmp_path):
    store=LocalStore(tmp_path);project=store.create_project('demo_app');source=store.create_source(project['id'],'api','dev','push',None)
    async def failing(self,since,until):
        yield parse_event({'timestamp':since.isoformat(),'message':'request timeout'})
        raise ConnectorError('railway_cli_failed')
    config={'project_id':project['id'],'since':'2026-10-01T00:00:00+00:00',
            'sources':[{'id':source['id'],'service':'api','environment':'dev','command_prefix':['railway']}]}
    with patch.object(RailwayCLIConnector,'fetch',failing):result=asyncio.run(collect_once(tmp_path,config))
    assert result[0]['status']=='collection_failed'
    assert store.evidence(project['id'])==[]
    with store.connection() as connection:assert connection.execute('SELECT count(*) FROM collection_cursors').fetchone()[0]==0


def test_variable_payloads_compact_into_one_retained_pattern(tmp_path):
    store=LocalStore(tmp_path);project=store.create_project('demo_app');source=store.create_source(project['id'],'api','dev','push',None)
    events=[parse_event({'timestamp':'2026-10-01T10:01:00Z','service':'api','level':'error',
                         'message':f'connection unavailable request payload-{index:x}', 'duration_ms':index,
                         'http_status':503,'request_status':503}) for index in range(50)]
    result=store.ingest(project['id'],source['id'],events)
    assert result=={'accepted':50,'summaries':1}
    rows=store.evidence(project['id'],'unavailable')
    assert rows[0]['event_count']==50 and rows[0]['duration_count']==50
    assert rows[0]['status_counts']=={'503':50}
    assert rows[0]['duration_sum_ms']==sum(range(50))


def test_question_boilerplate_does_not_match_every_memory_block(tmp_path):
    store=LocalStore(tmp_path);project=store.create_project('demo_app');source=store.create_source(project['id'],'api','dev','push',None)
    store.ingest(project['id'],source['id'],[parse_event({'timestamp':'2026-10-01T10:01:00Z','service':'api','message':message})
        for message in ['database connection timeout','cache redis unavailable','worker queue retry failed']])
    rows=store.evidence(project['id'],'Which database timeouts were observed?')
    assert len(rows)==1 and 'database' in rows[0]['summary']
    assert 'observed' not in store._fts_query('Which database timeouts were observed?')


def test_rate_limit_backoff_survives_restart_and_skips_provider(tmp_path):
    store=LocalStore(tmp_path);project=store.create_project('demo_app');source=store.create_source(project['id'],'api','dev','push',None)
    config={'project_id':project['id'],'since':'2026-10-01T00:00:00+00:00',
            'sources':[{'id':source['id'],'service':'api','environment':'dev','command_prefix':['railway']}]}
    async def limited(self,since,until):
        if False:yield
        raise RailwayRateLimited(2700)
    with patch.object(RailwayCLIConnector,'fetch',limited):first=asyncio.run(collect_once(tmp_path,config))
    assert first[0]['status']=='rate_limited'
    with patch.object(RailwayCLIConnector,'fetch') as fetch:
        second=asyncio.run(collect_once(tmp_path,config))
        fetch.assert_not_called()
    assert second[0]['retry_at']==first[0]['retry_at'] and second[0]['cursor'] is None


def test_cli_rate_limit_retry_duration_is_safe_and_numeric():
    connector=RailwayCLIConnector({'service':'api'});now=datetime.now(timezone.utc)
    result=SimpleNamespace(returncode=1,stdout=b'',stderr=b'You are being ratelimited. Try again in about 45 minutes token=private-value')
    with patch('connectors.railway_cli.subprocess.run',return_value=result):
        with pytest.raises(RailwayRateLimited) as error:connector._read(now,now+timedelta(hours=1))
    assert error.value.retry_after==2700 and 'private' not in str(error.value)


def recovery_fixture(tmp_path):
    store = LocalStore(tmp_path)
    project = store.create_project('synthetic')
    source = store.create_source(project['id'], 'api', 'dev', 'push', None)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    config = {'project_id': project['id'], 'since': start.isoformat(), 'sources': [
        {'id': source['id'], 'service': 'api', 'environment': 'dev', 'command_prefix': ['railway']}]}
    return store, source, start, config


def expire_source_retry(store, source):
    with store.connection(write=True) as connection:
        connection.execute('UPDATE collection_recovery SET retry_at=? WHERE source_id=?',
                           ('2000-01-01T00:00:00+00:00', source['id']))


def test_10001_events_shrink_window_then_commit_all_without_replay(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    # Real adapter splitting, with synthetic provider pages capped at 1000 lines.
    timestamps = [start + timedelta(seconds=index * 3600 / 10001) for index in range(10001)]
    calls = []

    def read(self, left, right):
        calls.append((left, right))
        selected = [ts for ts in timestamps if left <= ts < right]
        if len(selected) >= 1000:
            raise ConnectorError('railway_cli_window_capped')
        return [parse_event({'timestamp': ts.isoformat(), 'message': 'request timeout'}) for ts in selected]

    with patch.object(RailwayCLIConnector, '_read', read):
        first = asyncio.run(collect_once(tmp_path, config))[0]
        assert first['error_code'] == 'collection_memory_limit'
        assert first['cursor'] is None and first['window_seconds'] == 1800
        assert store.evidence(config['project_id']) == []
        with store.connection() as connection:
            assert connection.execute('SELECT count(*) FROM collection_cursors').fetchone()[0] == 0
            assert connection.execute('SELECT count(*) FROM coverage').fetchone()[0] == 0
        count = len(calls)
        second = asyncio.run(collect_once(tmp_path, config))[0]
        assert second == first and len(calls) == count  # Durable cooldown after restart.
        expire_source_retry(store, source)
        third = asyncio.run(collect_once(tmp_path, config))[0]
        assert third['accepted'] == 5001
        assert third['cursor'] == (start + timedelta(minutes=30)).isoformat()
        fourth = asyncio.run(collect_once(tmp_path, config))[0]
        assert fourth['accepted'] == 5000
        assert fourth['cursor'] == (start + timedelta(hours=1)).isoformat()
    assert sum(row['event_count'] for row in store.evidence(config['project_id'])) == 10001
    with store.connection() as connection:
        recovery = connection.execute('SELECT * FROM collection_recovery').fetchone()
        assert recovery['window_seconds'] == 1800  # Successful dense windows stay small.
        assert recovery['failures'] == 0 and recovery['retry_at'] is None


def test_127_subwindow_budget_failure_shrinks_without_partial_commit(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    calls = []

    def read(self, left, right):
        calls.append((left, right))
        if right-left > timedelta(seconds=30):
            raise ConnectorError('railway_cli_window_capped')
        return [parse_event({'timestamp': left.isoformat(), 'message': 'worker retry failed'})]

    with patch.object(RailwayCLIConnector, '_read', read):
        first = asyncio.run(collect_once(tmp_path, config))[0]
        assert len(calls) == 127 and first['error_code'] == 'railway_cli_window_capped'
        assert first['cursor'] is None and first['window_seconds'] == 1800
        assert store.evidence(config['project_id']) == []
        with store.connection() as connection:
            assert connection.execute('SELECT count(*) FROM coverage').fetchone()[0] == 0
        expire_source_retry(store, source)
        calls.clear()
        second = asyncio.run(collect_once(tmp_path, config))[0]
    assert len(calls) == 127 and second['accepted'] == 64
    assert second['cursor'] == (start + timedelta(minutes=30)).isoformat()


def test_generic_failures_back_off_to_one_hour_without_private_error_text(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    calls = []

    async def failing(self, since, until):
        calls.append((since, until))
        yield parse_event({'timestamp': since.isoformat(), 'message': 'PRIVATE-CANARY timeout'})
        raise RuntimeError('PRIVATE-CANARY token=secret')

    with patch.object(RailwayCLIConnector, 'fetch', failing):
        for delay in (60, 120, 240, 480, 960, 1920, 3600, 3600):
            before = datetime.now(timezone.utc)
            outcome = asyncio.run(collect_once(tmp_path, config))[0]
            assert delay <= (datetime.fromisoformat(outcome['retry_at'])-before).total_seconds() < delay+5
            assert outcome['cursor'] is None and outcome['window_seconds'] == 3600
            assert outcome['error_code'] == 'collection_failed'
            count = len(calls)
            assert asyncio.run(collect_once(tmp_path, config))[0] == outcome
            assert len(calls) == count
            expire_source_retry(store, source)
    assert len(set(calls)) == 1 and store.evidence(config['project_id']) == []
    assert b'PRIVATE-CANARY' not in store.db_path.read_bytes()
    with store.connection() as connection:
        assert connection.execute('SELECT failures FROM collection_recovery').fetchone()[0] == 7


def test_saturation_at_one_second_remains_failed_without_skipping_history(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    with store.connection(write=True) as connection:
        connection.execute('INSERT INTO collection_recovery VALUES(?,1,0,NULL,NULL)', (source['id'],))

    async def capped(self, since, until):
        assert until-since == timedelta(seconds=1)
        if False:
            yield
        raise ConnectorError('railway_cli_window_capped')

    with patch.object(RailwayCLIConnector, 'fetch', capped):
        outcome = asyncio.run(collect_once(tmp_path, config))[0]
    assert outcome['cursor'] is None and outcome['window_seconds'] == 1
    assert outcome['status'] == 'collection_failed' and 'retry_at' in outcome
    assert store.evidence(config['project_id']) == []


def test_shared_cooldown_stops_active_split_and_queued_sources_without_commit(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    config['sources'][0]['service'] = 'limited'
    for service in ('splitting', 'independent', 'queued'):
        added = store.create_source(config['project_id'], service, 'dev', 'push', None)
        config['sources'].append({'id': added['id'], 'service': service, 'environment': 'dev',
                                  'command_prefix': ['other-profile'] if service == 'independent' else ['railway']})
    sibling_pending = threading.Event()
    release = threading.Event()
    calls = []

    def read(self, left, right):
        calls.append((self.service, left, right))
        if self.service == 'limited':
            assert sibling_pending.wait(5)
            raise RailwayRateLimited(2700)
        if self.service == 'independent':
            assert release.wait(5)
            return []
        assert self.service == 'splitting'  # Queued shared source must never invoke CLI.
        if right-left > timedelta(minutes=30):
            raise ConnectorError('railway_cli_window_capped')
        if left > start:
            sibling_pending.set()
            assert release.wait(5)
        return [parse_event({'timestamp': left.isoformat(), 'message': 'PARTIAL-CANARY timeout'})]

    async def exercise():
        task = asyncio.create_task(collect_once(tmp_path, config))
        try:
            async with asyncio.timeout(5):
                while True:
                    with store.connection() as connection:
                        backoff = connection.execute('SELECT retry_at FROM collection_backoff').fetchone()
                    if backoff:
                        break
                    await asyncio.sleep(0.01)
            deadline = backoff['retry_at']
        finally:
            release.set()
            outcomes = await task
        return outcomes, deadline

    with patch.object(RailwayCLIConnector, '_read', read):
        outcomes, deadline = asyncio.run(exercise())
        by_service = {item['service']: item for item in outcomes}
        for service in ('limited', 'splitting', 'queued'):
            assert by_service[service]['status'] == 'rate_limited'
            assert by_service[service]['retry_at'] == deadline
            assert by_service[service]['cursor'] is None
        assert by_service['independent']['status'] == 'observed'
        assert by_service['independent']['accepted'] == 0
        assert len([call for call in calls if call[0] == 'splitting']) == 3
        assert store.evidence(config['project_id']) == []
        with store.connection() as connection:
            assert connection.execute('SELECT count(*) FROM collection_cursors').fetchone()[0] == 1
            assert connection.execute('SELECT count(*) FROM collection_recovery WHERE failures>0').fetchone()[0] == 0
            assert connection.execute('SELECT retry_at FROM collection_backoff').fetchone()[0] == deadline
        count = len(calls)
        # Restart preserves the shared deadline; unrelated profile can still collect.
        again = asyncio.run(collect_once(tmp_path, config))
        assert len(calls) == count+1
        assert all(item['retry_at'] == deadline for item in again if item['status'] == 'rate_limited')
    assert b'PARTIAL-CANARY' not in store.db_path.read_bytes()


def test_cooldown_is_checked_before_each_recursive_read():
    from connectors.railway_cli import RailwayCollectionDeferred

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    deadline = '2026-10-03T20:00:00+00:00'
    calls = []

    def guard():
        if calls:
            raise RailwayCollectionDeferred(deadline)

    def read(left, right):
        calls.append((left, right))
        raise ConnectorError('railway_cli_window_capped')

    connector = RailwayCLIConnector({'service': 'api'}, check_cooldown=guard)

    async def fetch():
        return [event async for event in connector.fetch(start, start+timedelta(hours=1))]

    with patch.object(connector, '_read', read):
        with pytest.raises(RailwayCollectionDeferred) as error:
            asyncio.run(fetch())
    assert error.value.retry_at == deadline and len(calls) == 1


def test_quiet_source_keeps_normal_interval_during_recovery_and_restart(tmp_path):
    store, _, start, config = recovery_fixture(tmp_path)
    config['interval'] = 300
    quiet = store.create_source(config['project_id'], 'quiet', 'dev', 'push', None)
    failed = store.create_source(config['project_id'], 'failed', 'dev', 'push', None)
    for source in (quiet, failed):
        config['sources'].append({'id': source['id'], 'service': source['name'],
                                  'environment': 'dev', 'command_prefix': ['railway']})
    clock = [start + timedelta(hours=4)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    quiet_cursor = (clock[0]-timedelta(seconds=60)).isoformat()
    store.ingest(config['project_id'], quiet['id'], [], checkpoint=(None, quiet_cursor))
    calls = {'api': [], 'quiet': [], 'failed': []}

    async def fetch(self, since, until):
        calls[self.service].append((since, until))
        if self.service == 'failed':
            raise ConnectorError('railway_cli_failed')
        if self.service == 'api' and len(calls['api']) == 1:
            raise ConnectorError('railway_cli_window_capped')
        if self.service == 'api':
            yield parse_event({'timestamp': since.isoformat(), 'message': 'worker timeout'})
        # Quiet windows genuinely return no events.

    with patch('logchat.local.collection.datetime', FrozenDatetime), patch.object(RailwayCLIConnector, 'fetch', fetch):
        first = {item['service']: item for item in asyncio.run(collect_once(tmp_path, config))}
        assert first['quiet']['accepted'] == 0
        deadline = first['quiet']['next_poll_at']
        assert deadline == (clock[0]+timedelta(seconds=300)).isoformat()
        assert first['api']['window_seconds'] == 1800
        assert first['failed']['status'] == 'collection_failed'
        for elapsed in (10, 60, 70, 120):
            clock[0] = start+timedelta(hours=4, seconds=elapsed)
            # Each invocation reopens the store, as a restarted collector does.
            outcomes = {item['service']: item for item in asyncio.run(collect_once(tmp_path, config))}
            assert outcomes['quiet']['cursor'] == first['quiet']['cursor']
            assert outcomes['quiet']['next_poll_at'] == deadline
            assert outcomes['quiet']['status'] == 'cached'
            assert outcomes['quiet']['provider_polled'] is False
            assert 'accepted' not in outcomes['quiet']
        assert len(calls['quiet']) == 1
        assert len(calls['api']) == 4  # Failure cooldown, then smaller successful backlog windows.
        assert len(calls['failed']) == 2  # 60s first cooldown; 120s second cooldown.
        with store.connection() as connection:
            assert connection.execute('SELECT count(*) FROM coverage WHERE source_id=?', (quiet['id'],)).fetchone()[0] == 0
            assert connection.execute('SELECT count(*) FROM collection_polling').fetchone()[0] == 1
        clock[0] = start+timedelta(hours=4, seconds=300)
        due = {item['service']: item for item in asyncio.run(collect_once(tmp_path, config))}
        assert due['quiet']['accepted'] == 0 and len(calls['quiet']) == 2
        assert calls['quiet'][1][0].isoformat() == first['quiet']['cursor']
        assert due['quiet']['cursor'] == (clock[0]-timedelta(seconds=30)).isoformat()
    assert sum(row['event_count'] for row in store.evidence(config['project_id'])) == 4


def test_cached_aged_cursor_does_not_trigger_catchup_loop(tmp_path):
    import subprocess
    import sys
    from logchat.local.collection import run

    store, source, start, config = recovery_fixture(tmp_path)
    config['interval'] = 300
    clock = [start + timedelta(hours=4)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    # Execute a real local synthetic CLI process through the actual adapter.
    # Empty stdout is a successful empty window, not live Railway evidence.
    script = tmp_path / 'synthetic_cli.py'
    script.write_text('pass\n')
    config['sources'][0]['command_prefix'] = [sys.executable, str(script)]
    (tmp_path / 'railway-collection.json').write_text(json.dumps(config))
    cursor = (clock[0]-timedelta(seconds=60)).isoformat()
    store.ingest(config['project_id'], source['id'], [], checkpoint=(None, cursor))
    sleeps = []

    class StopLoop(Exception):
        pass

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            # An early wake after three minutes makes the cached cursor aged,
            # but its five-minute polling deadline still forbids a new fetch.
            clock[0] += timedelta(seconds=180)
        else:
            raise StopLoop()

    with patch('logchat.local.collection.datetime', FrozenDatetime), \
            patch('logchat.local.collection.time', SimpleNamespace(sleep=sleep)), \
            patch('connectors.railway_cli.subprocess.run', wraps=subprocess.run) as cli:
        with pytest.raises(StopLoop):
            run(tmp_path)
        assert cli.call_count == 1
        assert sleeps == [300, 120]
        status = json.loads((tmp_path/'railway-status.json').read_text())['sources'][0]
        assert status['status'] == 'cached' and status['provider_polled'] is False
        assert 'accepted' not in status and 'message_normalization_counts' not in status
        # Restart at the persisted deadline must perform exactly one fresh poll.
        clock[0] = datetime.fromisoformat(status['next_poll_at'])
        restarted = run(tmp_path, once=True)[0]
        assert cli.call_count == 2
        assert restarted['status'] == 'observed' and restarted['provider_polled'] is True
        assert restarted['accepted'] == 0


def test_polling_deadline_is_atomic_with_checkpoint_and_migrates_existing_store(tmp_path):
    store, source, start, config = recovery_fixture(tmp_path)
    # Simulate an existing database predating the additive polling table.
    with store.connection(write=True) as connection:
        connection.execute('DROP TABLE collection_polling')
    store = LocalStore(tmp_path)
    deadline = (start+timedelta(minutes=5)).isoformat()
    cursor = (start+timedelta(minutes=1)).isoformat()
    store.ingest(config['project_id'], source['id'], [], checkpoint=(None, cursor),
                 collection_window_seconds=3600, collection_next_poll_at=deadline)
    with pytest.raises(RuntimeError, match='cursor changed'):
        store.ingest(config['project_id'], source['id'], [], checkpoint=(None, start.isoformat()),
                     collection_window_seconds=1800, collection_next_poll_at=start.isoformat())
    with store.connection() as connection:
        assert connection.execute('SELECT next_poll_at FROM collection_polling').fetchone()[0] == deadline
        assert connection.execute('SELECT cursor FROM collection_cursors').fetchone()[0] == cursor
    # Successful backlog ingestion clears a previous normal deadline atomically.
    store.ingest(config['project_id'], source['id'], [],
                 checkpoint=(cursor, (start+timedelta(minutes=2)).isoformat()), collection_window_seconds=1800)
    with store.connection() as connection:
        assert connection.execute('SELECT count(*) FROM collection_polling').fetchone()[0] == 0


@pytest.mark.parametrize('field,value,code', [
    ('interval', 0, 'invalid_collection_interval'),
    ('interval', -1, 'invalid_collection_interval'),
    ('interval', True, 'invalid_collection_interval'),
    ('interval', 1.5, 'invalid_collection_interval'),
    ('interval', '300', 'invalid_collection_interval'),
    ('interval', 86401, 'invalid_collection_interval'),
    ('since', None, 'invalid_collection_since'),
    ('since', 'PRIVATE-CANARY', 'invalid_collection_since'),
    ('since', '2026-10-01T00:00:00', 'invalid_collection_since'),
    ('since', '2999-01-01T00:00:00Z', 'invalid_collection_since'),
    ('project_id', 'PRIVATE-CANARY', 'invalid_collection_binding'),
    ('sources', None, 'invalid_collection_sources'),
    ('sources', [], 'invalid_collection_sources'),
])
def test_invalid_config_rejected_before_any_provider_call(tmp_path, field, value, code):
    store, _, _, config = recovery_fixture(tmp_path)
    project_id = config['project_id']
    config[field] = value
    with patch.object(RailwayCLIConnector, 'fetch') as fetch:
        with pytest.raises(ValueError) as error:
            asyncio.run(collect_once(tmp_path, config))
        fetch.assert_not_called()
    assert str(error.value) == code and 'PRIVATE-CANARY' not in str(error.value)
    assert store.evidence(project_id) == []
    with store.connection() as connection:
        for table in ('collection_cursors', 'collection_recovery', 'collection_polling', 'collection_backoff'):
            assert connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.parametrize('change,code', [
    ({'id': 'unknown'}, 'invalid_collection_binding'),
    ({'environment': 'other'}, 'invalid_collection_binding'),
    ({'service': ''}, 'invalid_collection_sources'),
    ({'command_prefix': 'railway'}, 'invalid_cli_prefix'),
    ({'command_prefix': []}, 'invalid_cli_prefix'),
    ({'command_prefix': ['railway', None]}, 'invalid_cli_prefix'),
    ({'command_prefix': ['railway', 'PRIVATE-CANARY\x00']}, 'invalid_cli_prefix'),
])
def test_invalid_sibling_prevents_partial_collection(tmp_path, change, code):
    store, _, _, config = recovery_fixture(tmp_path)
    sibling = store.create_source(config['project_id'], 'worker', 'dev', 'push', None)
    config['sources'].append({**config['sources'][0], 'id': sibling['id'], 'service': 'worker', **change})
    with patch.object(RailwayCLIConnector, 'fetch') as fetch:
        with pytest.raises(ValueError, match=code):
            asyncio.run(collect_once(tmp_path, config))
        fetch.assert_not_called()
    assert store.evidence(config['project_id']) == []


def test_cross_project_and_duplicate_sources_rejected(tmp_path):
    store, _, _, config = recovery_fixture(tmp_path)
    other_project = store.create_project('other')
    other_source = store.create_source(other_project['id'], 'api', 'dev', 'push', None)
    for source, code in ((config['sources'][0], 'invalid_collection_sources'),
                         ({**config['sources'][0], 'id': other_source['id']}, 'invalid_collection_binding')):
        candidate = {**config, 'sources': [config['sources'][0], source]}
        with patch.object(RailwayCLIConnector, 'fetch') as fetch:
            with pytest.raises(ValueError, match=code):
                asyncio.run(collect_once(tmp_path, candidate))
            fetch.assert_not_called()


def test_18_sources_offset_since_and_interval_bounds_remain_supported(tmp_path):
    store, _, _, config = recovery_fixture(tmp_path)
    config['since'] = '2026-10-01T03:00:00+03:00'
    for index in range(17):
        source = store.create_source(config['project_id'], f'worker-{index}', 'dev', 'push', None)
        config['sources'].append({**config['sources'][0], 'id': source['id'], 'service': source['name']})
    calls = []

    async def fetch(self, since, until):
        calls.append((since, until))
        if False:
            yield

    with patch.object(RailwayCLIConnector, 'fetch', fetch):
        for interval in (1, 86400):
            config['interval'] = interval
            outcomes = asyncio.run(collect_once(tmp_path, config))
            assert len(outcomes) == 18 and all(row['accepted'] == 0 for row in outcomes)
    assert len(calls) == 36
    assert calls[0][0] == datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize('fields', [
    {'timestamp': None, 'ts': '2026-10-01T00:00:00Z'},
    {'timestamp': '', 'time': '2026-10-01T00:00:00Z'},
    {'timestamp': '2026-10-01T00:00:00'},
    {'timestamp': True}, {'timestamp': float('nan')},
    {'timestamp': float('inf')}, {'timestamp': 1e100},
    {'timestamp': '0001-01-01T00:00:00+23:00'},
])
def test_provider_requires_explicit_valid_instant_without_alias_fallback(fields):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    output = json.dumps({'message': None, **fields}).encode()
    connector = RailwayCLIConnector({'service': 'api'})
    with patch('connectors.railway_cli.subprocess.run', return_value=SimpleNamespace(
            returncode=0, stdout=output, stderr=b'')):
        with pytest.raises(ConnectorError, match='railway_cli_invalid_event'):
            asyncio.run(_fetch_events(connector, start, start+timedelta(hours=1)))
    assert connector.message_normalization_counts == {'missing': 0, 'null': 0, 'empty': 0}


async def _fetch_events(connector, start, end):
    return [event async for event in connector.fetch(start, end)]


@pytest.mark.parametrize('fields', [
    {'timestamp': 0}, {'ts': 0.0}, {'time': '1970-01-01T03:00:00+03:00'},
    {'@timestamp': '1970-01-01T00:00:00Z'},
])
def test_provider_timestamp_aliases_offsets_and_epoch_zero(fields):
    start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    output = json.dumps({'message': None, **fields}).encode()
    connector = RailwayCLIConnector({'service': 'api'})
    with patch('connectors.railway_cli.subprocess.run', return_value=SimpleNamespace(
            returncode=0, stdout=output, stderr=b'')):
        events = asyncio.run(_fetch_events(connector, start, start+timedelta(seconds=1)))
    assert len(events) == 1 and events[0].ts == start
    assert connector.message_normalization_counts['null'] == 1


@pytest.mark.parametrize('next_cursor,code', [
    ('2026-10-01T00:00:00Z', 'collection_cursor_not_increasing'),
    ('2026-09-30T23:59:59Z', 'collection_cursor_not_increasing'),
    ('2026-10-01T03:00:00+03:00', 'collection_cursor_not_increasing'),
    ('2026-10-01T00:01:00', 'invalid_collection_timestamp'),
    ('PRIVATE-CANARY', 'invalid_collection_timestamp'),
    (None, 'invalid_collection_timestamp'),
])
def test_checkpoint_rejects_nonincreasing_or_invalid_time_atomically(tmp_path, next_cursor, code):
    store, source, _, config = recovery_fixture(tmp_path)
    cursor = '2026-10-01T00:00:00Z'
    store.ingest(config['project_id'], source['id'], [], checkpoint=(None, cursor),
                 collection_window_seconds=3600, collection_next_poll_at='2026-10-01T00:05:00Z')
    event = parse_event({'timestamp': cursor, 'message': 'timeout'})
    with pytest.raises(ValueError, match=code):
        store.ingest(config['project_id'], source['id'], [event], checkpoint=(cursor, next_cursor),
                     collection_window_seconds=1800)
    assert store.evidence(config['project_id']) == []
    with store.connection() as connection:
        assert connection.execute('SELECT cursor FROM collection_cursors').fetchone()[0] == cursor
        assert connection.execute('SELECT window_seconds FROM collection_recovery').fetchone()[0] == 3600
        assert connection.execute('SELECT next_poll_at FROM collection_polling').fetchone()[0] == '2026-10-01T00:05:00Z'
    # Ordering uses instants, rather than lexical ordering of offset strings.
    store.ingest(config['project_id'], source['id'], [],
                 checkpoint=(cursor, '2026-09-30T23:01:00-01:00'))


@pytest.mark.parametrize('cursor,code', [
    ('PRIVATE-CANARY', 'invalid_collection_timestamp'),
    ('2026-10-01T00:00:00', 'invalid_collection_timestamp'),
    ('2999-01-01T00:00:00Z', 'invalid_collection_cursor'),
])
def test_invalid_saved_sibling_cursor_blocks_whole_pass(tmp_path, cursor, code):
    store, _, _, config = recovery_fixture(tmp_path)
    sibling = store.create_source(config['project_id'], 'worker', 'dev', 'push', None)
    config['sources'].append({**config['sources'][0], 'id': sibling['id'], 'service': 'worker'})
    with store.connection(write=True) as connection:
        connection.execute('INSERT INTO collection_cursors VALUES(?,?)', (sibling['id'], cursor))
    with patch.object(RailwayCLIConnector, 'fetch') as fetch:
        with pytest.raises(ValueError, match=code):
            asyncio.run(collect_once(tmp_path, config))
        fetch.assert_not_called()
    assert store.evidence(config['project_id']) == []


@pytest.mark.parametrize('table,field,value', [
    *[(table, field, value)
      for table, field in [('collection_backoff', 'retry_at'),
                           ('collection_polling', 'next_poll_at'),
                           ('collection_recovery', 'retry_at')]
      for value in ('PRIVATE-CANARY', '', '2026-10-01T00:00:00',
                    '2999-01-01T00:00:00Z', '0001-01-01T00:00:00+23:00')],
    ('collection_recovery', 'retry_at', None),
    *[('collection_recovery', 'window_seconds', value) for value in (0, 3601, 1.5, 'PRIVATE-CANARY')],
    *[('collection_recovery', 'failures', value) for value in (-1, 8, 1.5, 'PRIVATE-CANARY')],
    ('collection_recovery', 'error_code', 'PRIVATE-CANARY'),
    ('collection_recovery', 'error_code', None),
    ('collection_recovery', 'failures', 0),
])
def test_invalid_saved_schedule_blocks_all_cli_calls_and_binding_on_restart(tmp_path, table, field, value):
    import hashlib
    import subprocess
    import sys

    store, _, start, config = recovery_fixture(tmp_path)
    sibling = store.create_source(config['project_id'], 'worker', 'dev', 'push', None)
    script = tmp_path / 'synthetic_cli.py'
    script.write_text('pass\n')
    config['sources'][0]['command_prefix'] = [sys.executable, str(script)]
    # Separate scope: corruption in a later sibling must block the first one too.
    config['sources'].append({**config['sources'][0], 'id': sibling['id'], 'service': 'worker',
                              'command_prefix': [sys.executable, str(script), 'worker']})
    clock = start + timedelta(hours=4)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz)

    scope = hashlib.sha256(json.dumps(config['sources'][1]['command_prefix']).encode()).hexdigest()
    key = scope if table == 'collection_backoff' else sibling['id']
    key_field = 'scope' if table == 'collection_backoff' else 'source_id'
    with store.connection(write=True) as connection:
        # Simulate corruption even when SQLite normally enforces numeric bounds.
        connection.execute('PRAGMA ignore_check_constraints=ON')
        if table == 'collection_recovery':
            connection.execute('INSERT INTO collection_recovery VALUES(?,3600,1,?,?)',
                               (key, (clock+timedelta(seconds=60)).isoformat(), 'collection_failed'))
        else:
            connection.execute(f'INSERT INTO {table} VALUES(?,?)', (key, clock.isoformat()))
        connection.execute(f'UPDATE {table} SET {field}=? WHERE {key_field}=?', (value, key))
        before = [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
    with patch('logchat.local.collection.datetime', FrozenDatetime), \
            patch('connectors.railway_cli.subprocess.run', wraps=subprocess.run) as cli:
        for _ in range(2):
            with pytest.raises(ValueError) as error:
                asyncio.run(collect_once(tmp_path, config))
            assert str(error.value) == 'invalid_collection_schedule'
            assert cli.call_count == 0
            # Reopen independently: preflight rolls back first-sibling bindings,
            # and preserves the corrupt row for explicit operator repair.
            with LocalStore(tmp_path).connection() as connection:
                assert connection.execute('SELECT count(*) FROM collection_bindings').fetchone()[0] == 0
                assert connection.execute('SELECT count(*) FROM collection_cursors').fetchone()[0] == 0
                assert [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')] == before
        with store.connection(write=True) as connection:
            connection.execute(f'DELETE FROM {table} WHERE {key_field}=?', (key,))
        outcomes = asyncio.run(collect_once(tmp_path, config))
        assert cli.call_count == 2 and all(row['provider_polled'] for row in outcomes)
    assert store.evidence(config['project_id']) == []


def test_saved_schedule_accepts_offset_deadlines_and_producer_bounds(tmp_path):
    import hashlib

    store, source, start, config = recovery_fixture(tmp_path)
    clock = start + timedelta(hours=4)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz)

    offset = timezone(timedelta(hours=3))
    scope = hashlib.sha256(json.dumps(config['sources'][0]['command_prefix']).encode()).hexdigest()
    with store.connection(write=True) as connection:
        connection.execute('INSERT INTO collection_backoff VALUES(?,?)',
                           (scope, (clock+timedelta(seconds=86430)).astimezone(offset).isoformat()))
        connection.execute('INSERT INTO collection_polling VALUES(?,?)',
                           (source['id'], (clock+timedelta(seconds=86400)).isoformat()))
        connection.execute('INSERT INTO collection_recovery VALUES(?,1,7,?,?)',
                           (source['id'], (clock+timedelta(seconds=3600)).isoformat(), 'collection_failed'))
    with patch('logchat.local.collection.datetime', FrozenDatetime), \
            patch.object(RailwayCLIConnector, 'fetch') as fetch:
        assert asyncio.run(collect_once(tmp_path, config))[0]['status'] == 'rate_limited'
        fetch.assert_not_called()


@pytest.mark.parametrize('change', [
    {'command_prefix': ['railway', '--profile', 'PRIVATE-PROFILE-CANARY']},
    {'service': 'other-service'},
])
def test_bound_profile_or_service_change_cannot_reuse_cursor(tmp_path, change):
    store, source, _, config = recovery_fixture(tmp_path)

    async def empty(self, since, until):
        if False:
            yield

    with patch.object(RailwayCLIConnector, 'fetch', empty):
        first = asyncio.run(collect_once(tmp_path, config))[0]
    with store.connection() as connection:
        before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                  for table in ('collection_bindings', 'collection_cursors', 'collection_recovery',
                                'collection_polling', 'collection_backoff')}
    config['sources'][0].update(change)
    with patch.object(RailwayCLIConnector, 'fetch') as fetch:
        with pytest.raises(ValueError) as error:
            asyncio.run(collect_once(tmp_path, config))
        assert str(error.value) == 'collection_binding_mismatch'
        fetch.assert_not_called()
    # Reopening the store must retain the original binding and scheduling state.
    store = LocalStore(tmp_path)
    with store.connection() as connection:
        after = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                 for table in before}
    assert before == after and first['cursor'] == before['collection_cursors'][0][1]
    assert b'PRIVATE-PROFILE-CANARY' not in store.db_path.read_bytes()


def test_legacy_binding_migration_preserves_deadlines_and_validates_whole_pass(tmp_path):
    import hashlib

    store, source, start, config = recovery_fixture(tmp_path)
    cursor = (start+timedelta(minutes=1)).isoformat()
    deadline = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    store.ingest(config['project_id'], source['id'], [], checkpoint=(None, cursor),
                 collection_window_seconds=1800, collection_next_poll_at=deadline)
    scope = hashlib.sha256(json.dumps(config['sources'][0]['command_prefix']).encode()).hexdigest()
    with store.connection(write=True) as connection:
        connection.execute('DROP TABLE collection_bindings')
        connection.execute('INSERT INTO collection_backoff VALUES(?,?)', (scope, deadline))
    # Old configuration has no new fields; first validated use pins it locally.
    with patch.object(RailwayCLIConnector, 'fetch') as fetch:
        outcome = asyncio.run(collect_once(tmp_path, config))[0]
        fetch.assert_not_called()
    assert outcome['status'] == 'rate_limited' and outcome['cursor'] == cursor
    assert outcome['retry_at'] == deadline
    with store.connection() as connection:
        assert connection.execute('SELECT count(*) FROM collection_bindings').fetchone()[0] == 1
        assert connection.execute('SELECT next_poll_at FROM collection_polling').fetchone()[0] == deadline
        assert connection.execute('SELECT window_seconds FROM collection_recovery').fetchone()[0] == 1800
    sibling = store.create_source(config['project_id'], 'worker', 'dev', 'push', None)
    candidate = {**config, 'sources': [
        {**config['sources'][0], 'id': sibling['id'], 'service': 'worker'},
        {**config['sources'][0], 'command_prefix': ['changed-profile']},
    ]}
    with patch.object(RailwayCLIConnector, 'fetch') as fetch:
        with pytest.raises(ValueError, match='collection_binding_mismatch'):
            asyncio.run(collect_once(tmp_path, candidate))
        fetch.assert_not_called()
    with store.connection() as connection:
        assert connection.execute('SELECT count(*) FROM collection_bindings').fetchone()[0] == 1


def test_loop_deadlines_mixed_sources_with_actual_synthetic_cli(tmp_path):
    import subprocess
    import sys
    from logchat.local.collection import run

    store, source, start, config = recovery_fixture(tmp_path)
    config['interval'] = 300
    clock = [start+timedelta(hours=4)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    script = tmp_path/'synthetic_cli.py'
    script.write_text('import sys\nservice = sys.argv[sys.argv.index("--service")+1]\n'
                      'sys.exit(1 if service == "failed" else 0)\n')
    prefix = [sys.executable, str(script)]
    config['sources'][0]['command_prefix'] = prefix
    cursors = {'api': clock[0]-timedelta(seconds=7300),
               'quiet': clock[0]-timedelta(seconds=60), 'no-work': clock[0]}
    for name in ('quiet', 'failed', 'no-work'):
        row = store.create_source(config['project_id'], name, 'dev', 'push', None)
        config['sources'].append({'id': row['id'], 'service': name,
                                  'environment': 'dev', 'command_prefix': prefix})
    for item in config['sources']:
        if item['service'] in cursors:
            store.ingest(config['project_id'], item['id'], [],
                         checkpoint=(None, cursors[item['service']].isoformat()))
    (tmp_path/'railway-collection.json').write_text(json.dumps(config))
    sleeps = []

    class StopLoop(Exception):
        pass

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise StopLoop()
        clock[0] += timedelta(seconds=seconds)

    with patch('logchat.local.collection.datetime', FrozenDatetime), \
            patch('logchat.local.collection.time', SimpleNamespace(sleep=sleep)), \
            patch('connectors.railway_cli.subprocess.run', wraps=subprocess.run) as cli:
        with pytest.raises(StopLoop):
            run(tmp_path)
        assert sleeps == [10, 50, 120]
        services = [call.args[0][call.args[0].index('--service')+1]
                    for call in cli.call_args_list]
        assert {name: services.count(name) for name in ('api', 'quiet', 'failed', 'no-work')} == {
            'api': 2, 'quiet': 1, 'failed': 2, 'no-work': 1}
        run(tmp_path, once=True)
        assert cli.call_count == 6


def test_loop_delay_respects_all_shared_and_source_deadlines(tmp_path):
    import hashlib
    from logchat.local.collection import loop_delay

    store, source, start, config = recovery_fixture(tmp_path)
    now = start+timedelta(hours=4)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    scope = hashlib.sha256(json.dumps(config['sources'][0]['command_prefix']).encode()).hexdigest()
    store.ingest(config['project_id'], source['id'], [],
                 checkpoint=(None, (now-timedelta(hours=2)).isoformat()))
    with store.connection(write=True) as connection:
        connection.execute('INSERT INTO collection_backoff VALUES(?,?)',
                           (scope, (now+timedelta(seconds=240)).isoformat()))
        connection.execute('INSERT INTO collection_polling VALUES(?,?)',
                           (source['id'], (now+timedelta(seconds=120)).isoformat()))
        connection.execute('INSERT INTO collection_recovery VALUES(?,3600,1,?,?)',
                           (source['id'], (now+timedelta(seconds=180)).isoformat(), 'collection_failed'))
    with patch('logchat.local.collection.datetime', FrozenDatetime):
        assert loop_delay(tmp_path, config) == 240
        # A sibling's later shared cooldown overrides eligible backlog work.
        with store.connection(write=True) as connection:
            connection.execute('UPDATE collection_backoff SET retry_at=?',
                               ((now+timedelta(seconds=600)).isoformat(),))
        assert loop_delay(tmp_path, config) == 300
        config['interval'] = 1
        assert loop_delay(tmp_path, config) == 1


@pytest.mark.parametrize('close_pipes_before_exit', [False, True])
def test_shared_retry_deadline_loop_and_restart_cli_call_counts(tmp_path, close_pipes_before_exit):
    import subprocess
    import sys
    from logchat.local.collection import run

    store, _, start, config = recovery_fixture(tmp_path)
    clock = [start+timedelta(hours=4)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    script = tmp_path/'limited_cli.py'
    # Closing captured pipes before exit also exercises subprocess's timed wait:
    # its real clock/sleep must remain independent of the collector's fake clock.
    script.write_text('import os, sys, time\n'
                      'print("too many requests 60 seconds", file=sys.stderr, flush=True)\n'
                      + ('os.close(1)\nos.close(2)\ntime.sleep(0.05)\n'
                         if close_pipes_before_exit else '')
                      + 'sys.exit(1)\n')
    prefix = [sys.executable, str(script)]
    config['sources'][0]['command_prefix'] = prefix
    for name in ('second', 'third', 'queued'):
        row = store.create_source(config['project_id'], name, 'dev', 'push', None)
        config['sources'].append({'id': row['id'], 'service': name,
                                  'environment': 'dev', 'command_prefix': prefix})
    (tmp_path/'railway-collection.json').write_text(json.dumps(config))
    sleeps = []

    class StopLoop(Exception):
        pass

    def sleep(seconds):
        sleeps.append(seconds)
        raise StopLoop()

    with patch('logchat.local.collection.datetime', FrozenDatetime), \
            patch('logchat.local.collection.time', SimpleNamespace(sleep=sleep)), \
            patch('connectors.railway_cli.subprocess.run', wraps=subprocess.run) as cli:
        with pytest.raises(StopLoop):
            run(tmp_path)
        assert sleeps == [90] and cli.call_count == 3
        assert all(item['status'] == 'rate_limited' for item in run(tmp_path, once=True))
        assert cli.call_count == 3
        clock[0] += timedelta(seconds=90)
        with pytest.raises(StopLoop):
            run(tmp_path)
        assert sleeps == [90, 90] and cli.call_count == 6


@pytest.mark.parametrize(('saved', 'proposed', 'expected'), [
    ('2026-10-03T17:50:00+02:00', '2026-10-03T16:20:30+00:00', '2026-10-03T16:20:30+00:00'),
    ('2026-10-03T12:30:00-04:00', '2026-10-03T16:20:30+00:00', '2026-10-03T12:30:00-04:00'),
    ('2026-10-03T18:20:30+02:00', '2026-10-03T16:20:30+00:00', '2026-10-03T18:20:30+02:00'),
    ('2026-10-03T16:30:00+00:00', '2026-10-03T16:20:30+00:00', '2026-10-03T16:30:00+00:00'),
])
def test_shared_backoff_compares_instants_and_preserves_existing_ties(tmp_path, saved, proposed, expected):
    store = LocalStore(tmp_path)
    with store.connection(write=True) as connection:
        connection.execute('INSERT INTO collection_backoff VALUES(?,?)', ('synthetic', saved))
    assert store.extend_collection_backoff('synthetic', proposed) == expected
    with LocalStore(tmp_path).connection() as connection:
        assert connection.execute('SELECT retry_at FROM collection_backoff').fetchone()[0] == expected


def test_concurrent_shared_backoff_extensions_keep_chronological_maximum(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from logchat.local.store import collection_time

    stores = [LocalStore(tmp_path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))
    deadlines = [(datetime(2026, 10, 3, 16, tzinfo=timezone.utc)+timedelta(minutes=index))
                 .astimezone(timezone(timedelta(hours=2 if index % 2 == 0 else -4))).isoformat()
                 for index in range(len(stores))]

    def extend(index):
        barrier.wait(timeout=10)
        return stores[index].extend_collection_backoff('synthetic', deadlines[index])

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(extend, range(len(stores))))
    assert all(collection_time(result) >= collection_time(proposed)
               for result, proposed in zip(results, deadlines))
    with stores[0].connection() as connection:
        assert connection.execute('SELECT retry_at FROM collection_backoff').fetchone()[0] == deadlines[-1]


def test_expired_offset_backoff_extends_and_defers_sibling_after_restart(tmp_path):
    import hashlib

    store, _, _, config = recovery_fixture(tmp_path)
    now = datetime(2026, 10, 3, 16, tzinfo=timezone.utc)
    scope = hashlib.sha256(json.dumps(config['sources'][0]['command_prefix']).encode()).hexdigest()
    with store.connection(write=True) as connection:
        connection.execute('INSERT INTO collection_backoff VALUES(?,?)',
                           (scope, '2026-10-03T17:50:00+02:00'))
    calls = []

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    class Limited:
        async def fetch(self, *args):
            calls.append(args)
            raise RailwayRateLimited(1200)
            yield

    with patch('logchat.local.collection.datetime', FrozenDatetime), \
            patch('logchat.local.collection.RailwayCLIConnector', return_value=Limited()):
        outcome = asyncio.run(collect_once(tmp_path, config))[0]
        deadline = '2026-10-03T16:20:30+00:00'
        assert outcome['status'] == 'rate_limited' and outcome['retry_at'] == deadline
        sibling = LocalStore(tmp_path).create_source(config['project_id'], 'worker', 'dev', 'push', None)
        config['sources'].append({**config['sources'][0], 'id': sibling['id'], 'service': 'worker'})
        again = asyncio.run(collect_once(tmp_path, config))
        assert len(calls) == 1
        assert all(item['status'] == 'rate_limited' and item['retry_at'] == deadline
                   and item['cursor'] is None for item in again)
        assert LocalStore(tmp_path).evidence(config['project_id']) == []
