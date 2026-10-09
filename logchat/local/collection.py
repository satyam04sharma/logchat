"""Persistent remote CLI collection into the native aggregate store."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
import time

from connectors.railway_cli import RailwayCLIConnector, RailwayRateLimited, RailwayCollectionDeferred
from connectors.base import ConnectorError
from logchat.local.lifecycle import locked, write_private
from logchat.local.store import LocalStore, collection_time


MAX_WINDOW_SECONDS = 3600
MAX_EVENTS = 10000
SATURATION_CODES = {'collection_memory_limit', 'railway_cli_window_capped'}
SAFE_FAILURE_CODES = SATURATION_CODES | {
    'railway_cli_unavailable', 'railway_cli_failed', 'railway_cli_output_limit',
    'railway_cli_invalid_event', 'invalid_collection_window', 'invalid_cli_prefix',
}


def validate_schedule(connection, source, now):
    """Reject corrupt persisted scheduling state before dispatching any sibling."""
    def deadline(value, maximum):
        try:
            parsed = collection_time(value)
            if parsed > now + timedelta(seconds=maximum):
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise ValueError('invalid_collection_schedule') from None

    scope = hashlib.sha256(json.dumps(source['command_prefix']).encode()).hexdigest()
    backoff = connection.execute('SELECT retry_at FROM collection_backoff WHERE scope=?',
                                 (scope,)).fetchone()
    if backoff:
        # Provider Retry-After is capped at one day, plus our 30-second margin.
        deadline(backoff['retry_at'], 86430)
    polling = connection.execute('SELECT next_poll_at FROM collection_polling WHERE source_id=?',
                                 (source['id'],)).fetchone()
    if polling:
        deadline(polling['next_poll_at'], 86400)
    recovery = connection.execute('SELECT * FROM collection_recovery WHERE source_id=?',
                                  (source['id'],)).fetchone()
    if recovery:
        if (type(recovery['window_seconds']) is not int or
                not 1 <= recovery['window_seconds'] <= MAX_WINDOW_SECONDS or
                type(recovery['failures']) is not int or not 0 <= recovery['failures'] <= 7):
            raise ValueError('invalid_collection_schedule')
        if recovery['failures'] == 0:
            if recovery['retry_at'] is not None or recovery['error_code'] is not None:
                raise ValueError('invalid_collection_schedule')
        else:
            deadline(recovery['retry_at'], 3600)
            if recovery['error_code'] not in SAFE_FAILURE_CODES | {'collection_failed'}:
                raise ValueError('invalid_collection_schedule')


def validate_config(config, store):
    """Validate the whole pass before any source can invoke its provider.

    Errors are fixed codes: configuration may contain private argv/scope values.
    This validates local binding, not the remote identity behind a CLI profile.
    """
    def text(value):
        return isinstance(value, str) and bool(value.strip()) and '\x00' not in value

    if not isinstance(config, dict) or not text(config.get('project_id')):
        raise ValueError('invalid_collection_config')
    interval = config.get('interval', 300)
    if type(interval) is not int or not 1 <= interval <= 86400:
        raise ValueError('invalid_collection_interval')
    try:
        since = datetime.fromisoformat(config['since'])
        if since.utcoffset() is None or since >= datetime.now(timezone.utc):
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('invalid_collection_since') from None
    sources = config.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('invalid_collection_sources')
    ids = set()
    for source in sources:
        if (not isinstance(source, dict) or
                not all(text(source.get(key)) for key in ('id', 'environment', 'service')) or
                source['id'] in ids):
            raise ValueError('invalid_collection_sources')
        ids.add(source['id'])
        prefix = source.get('command_prefix')
        if not isinstance(prefix, list) or not prefix or not all(text(arg) for arg in prefix):
            raise ValueError('invalid_cli_prefix')
    # Pin only after the whole pass validates. A mismatched sibling rolls back
    # new bindings too; no private argv is persisted in the identity table.
    with store.connection(write=True) as connection:
        now = datetime.now(timezone.utc)
        for source in sources:
            row = connection.execute('''SELECT e.name FROM sources s
                JOIN environments e ON e.id=s.environment_id AND e.project_id=s.project_id
                WHERE s.id=? AND s.project_id=?''', (source['id'], config['project_id'])).fetchone()
            if not row or row['name'] != source['environment']:
                raise ValueError('invalid_collection_binding')
            cursor = connection.execute('SELECT cursor FROM collection_cursors WHERE source_id=?',
                                        (source['id'],)).fetchone()
            if cursor and collection_time(cursor['cursor']) > datetime.now(timezone.utc):
                raise ValueError('invalid_collection_cursor')
            validate_schedule(connection, source, now)
            identity = [config['project_id'], source['id'], source['environment'],
                        source['service'], source['command_prefix']]
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True,
                                              separators=(',', ':')).encode()).hexdigest()
            binding = connection.execute('SELECT identity_hash FROM collection_bindings WHERE source_id=?',
                                         (source['id'],)).fetchone()
            if binding and binding['identity_hash'] != digest:
                raise ValueError('collection_binding_mismatch')
            connection.execute('INSERT OR IGNORE INTO collection_bindings VALUES(?,?)',
                               (source['id'], digest))


async def collect_once(directory, config):
    store = LocalStore(directory)
    validate_config(config, store)
    async def collect_source(source):
        scope = hashlib.sha256(json.dumps(source['command_prefix']).encode()).hexdigest()
        with store.connection() as connection:
            row = connection.execute('SELECT cursor FROM collection_cursors WHERE source_id=?', (source['id'],)).fetchone()
            backoff = connection.execute('SELECT retry_at FROM collection_backoff WHERE scope=?', (scope,)).fetchone()
            recovery = connection.execute('SELECT * FROM collection_recovery WHERE source_id=?', (source['id'],)).fetchone()
            polling = connection.execute('SELECT next_poll_at FROM collection_polling WHERE source_id=?', (source['id'],)).fetchone()
        expected = row['cursor'] if row else None
        identity = {'source_id': source['id'], 'environment': source['environment'], 'service': source['service']}
        def check_cooldown():
            with store.connection() as connection:
                current = connection.execute('SELECT retry_at FROM collection_backoff WHERE scope=?', (scope,)).fetchone()
            if current and datetime.fromisoformat(current['retry_at']) > datetime.now(timezone.utc):
                raise RailwayCollectionDeferred(current['retry_at'])

        if backoff and datetime.fromisoformat(backoff['retry_at']) > datetime.now(timezone.utc):
            return {**identity, 'status': 'rate_limited', 'cursor': expected, 'retry_at': backoff['retry_at']}
        if recovery and recovery['retry_at'] and datetime.fromisoformat(recovery['retry_at']) > datetime.now(timezone.utc):
            return {**identity, 'status': 'collection_failed', 'cursor': expected,
                    'retry_at': recovery['retry_at'], 'error_code': recovery['error_code'],
                    'window_seconds': recovery['window_seconds']}
        if expected and polling and datetime.fromisoformat(polling['next_poll_at']) > datetime.now(timezone.utc):
            # A retained cursor is not evidence of a provider poll in this pass.
            return {**identity, 'status': 'cached', 'provider_polled': False, 'cursor': expected,
                    'next_poll_at': polling['next_poll_at']}
        window_seconds = recovery['window_seconds'] if recovery else MAX_WINDOW_SECONDS
        since = collection_time(expected or config['since'])
        # Delay gives recently emitted provider logs time to arrive. This cannot prove completeness.
        until = min(since + timedelta(seconds=window_seconds), datetime.now(timezone.utc)-timedelta(seconds=30))
        if until <= since:
            return None
        connector = RailwayCLIConnector({'service': source['service'], 'command_prefix': source['command_prefix']},
                                        check_cooldown=check_cooldown)
        try:
            # Hold one bounded window; partial fetch failure cannot advance memory/cursor.
            events = []
            async for event in connector.fetch(since, until):
                if len(events) >= MAX_EVENTS:
                    raise ConnectorError('collection_memory_limit')
                events.append(event)
            check_cooldown()
            completed_at = datetime.now(timezone.utc)
            next_poll_at = ((completed_at + timedelta(seconds=config.get('interval', 300))).isoformat()
                            if until >= completed_at-timedelta(minutes=2) else None)
            result = store.ingest(config['project_id'], source['id'], events,
                                  checkpoint=(expected, until.isoformat()), collection_window_seconds=window_seconds,
                                  collection_next_poll_at=next_poll_at)
            return {**identity, **result, 'cursor': until.isoformat(), 'status': 'observed',
                    'provider_polled': True,
                    'message_normalization_counts': dict(connector.message_normalization_counts),
                    **({'next_poll_at': next_poll_at} if next_poll_at else {})}
        except RailwayCollectionDeferred as error:
            # Reuse the shared deadline; deferred siblings must not extend it.
            return {**identity, 'status': 'rate_limited', 'cursor': expected, 'retry_at': error.retry_at}
        except RailwayRateLimited as error:
            retry_at = (datetime.now(timezone.utc)+timedelta(seconds=error.retry_after+30)).isoformat()
            retry_at = store.extend_collection_backoff(scope, retry_at)
            return {**identity, 'status': 'rate_limited', 'cursor': expected, 'retry_at': retry_at}
        except (ConnectorError, RuntimeError) as error:
            # Never persist arbitrary exception text, provider stderr, or event payloads.
            code = str(error) if isinstance(error, ConnectorError) and str(error) in SAFE_FAILURE_CODES else 'collection_failed'
            if code in SATURATION_CODES:
                # Retry the same left boundary in a smaller window, never skip history.
                window_seconds = max(1, min(window_seconds, int((until-since).total_seconds())) // 2)
            failures = min(7, (recovery['failures'] if recovery else 0) + 1)
            delay = min(3600, 60 * 2 ** (failures-1))
            retry_at = (datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat()
            with store.connection(write=True) as connection:
                current = connection.execute('SELECT cursor FROM collection_cursors WHERE source_id=?', (source['id'],)).fetchone()
                # A stale attempt must not replace recovery state after another commit.
                if (current['cursor'] if current else None) == expected:
                    connection.execute('''INSERT INTO collection_recovery VALUES(?,?,?,?,?)
                        ON CONFLICT(source_id) DO UPDATE SET window_seconds=excluded.window_seconds,
                        failures=excluded.failures,retry_at=excluded.retry_at,error_code=excluded.error_code''',
                        (source['id'], window_seconds, failures, retry_at, code))
            return {**identity, 'status': 'collection_failed', 'cursor': expected,
                    'retry_at': retry_at, 'error_code': code, 'window_seconds': window_seconds}
    semaphore = asyncio.Semaphore(3)
    async def bounded(source):
        async with semaphore:
            return await collect_source(source)
    outcomes = await asyncio.gather(*(bounded(source) for source in config['sources']))
    return [item for item in outcomes if item is not None]


def loop_delay(directory, config):
    """Wake for the earliest eligible source, using committed shared deadlines.

    Re-read state after the pass: a sibling can impose a shared cooldown after
    another source has already reported a successful backlog window.
    """
    now = datetime.now(timezone.utc)
    cadence = min(300, config.get('interval', 300))
    delays = []
    with LocalStore(directory).connection() as connection:
        for source in config['sources']:
            validate_schedule(connection, source, now)
            cursor = connection.execute('SELECT cursor FROM collection_cursors WHERE source_id=?',
                                        (source['id'],)).fetchone()
            scope = hashlib.sha256(json.dumps(source['command_prefix']).encode()).hexdigest()
            shared = connection.execute('SELECT retry_at FROM collection_backoff WHERE scope=?',
                                        (scope,)).fetchone()
            recovery = connection.execute('SELECT retry_at FROM collection_recovery WHERE source_id=?',
                                          (source['id'],)).fetchone()
            polling = connection.execute('SELECT next_poll_at FROM collection_polling WHERE source_id=?',
                                         (source['id'],)).fetchone()
            deadlines = [now]
            for row, key in ((shared, 'retry_at'), (recovery, 'retry_at'),
                             (polling if cursor else None, 'next_poll_at')):
                if row and row[key]:
                    deadlines.append(collection_time(row[key]))
            eligible_at = max(deadlines)
            if eligible_at > now:
                delays.append((eligible_at-now).total_seconds())
            else:
                since = collection_time(cursor['cursor'] if cursor else config['since'])
                delays.append(10 if since < now-timedelta(minutes=2) else cadence)
    # Recheck at least every five minutes for config changes; never busy spin.
    return max(1, min(cadence, min(delays, default=cadence)))


def run(directory, once=False):
    directory = Path(directory)
    # Dedicated process lock prevents two collectors from counting the same window.
    lock_dir = directory/'collector-lock'
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    with locked(lock_dir):
        while True:
            config = json.loads((directory/'railway-collection.json').read_text())
            outcomes = asyncio.run(collect_once(directory, config))
            write_private(directory/'railway-status.json', {'updated_at': datetime.now(timezone.utc).isoformat(),
                'sources': outcomes, 'limitations': ['CLI history can be capped or expired; late arriving events may be missed.',
                'Only selected Railway deployment logs are collected; coverage is not continuous or complete.']})
            if once:
                return outcomes
            time.sleep(loop_delay(directory, config))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    run(args.state_dir)
