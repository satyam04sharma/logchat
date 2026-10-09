"""Persistent native provider collection, with observation cursors and leases.

This metadata database never receives event bodies. Provider windows are finite
observations, not evidence of complete remote coverage or retained history.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from uuid import UUID, uuid4

from connectors.provider_cli import ProviderError, fetch_window, timestamp

KINDS = frozenset({'docker', 'railway', 'vercel', 'cli'})
MAX_WINDOW_SECONDS = 300


def _now():
    return datetime.now(timezone.utc)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096 and '\0' not in value


def validate_config(kind, value):
    common = {'executable', 'cwd', 'token_env', 'token_ref', 'since', 'interval_seconds', 'window_seconds', 'timeout_seconds'}
    specific = {'docker': {'container'}, 'railway': {'provider_project', 'provider_environment', 'service'},
                'vercel': {'provider_project', 'provider_environment', 'scope'}, 'cli': {'argv'}}
    if kind not in KINDS or not isinstance(value, dict) or set(value) - common - specific[kind]:
        raise ValueError('invalid_provider_config')
    config = dict(value)
    required = specific[kind] - {'scope'}
    for key in (common - {'token_ref', 'since', 'interval_seconds', 'window_seconds', 'timeout_seconds'}) | required | {'scope'}:
        if key == 'argv':
            continue
        if key in config and not _text(config[key]) or key in required and key not in config:
            raise ValueError('invalid_provider_config')
    if kind == 'cli':
        argv = config.get('argv')
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 64 or not all(_text(arg) for arg in argv)
                or not any('{since}' in arg for arg in argv) or not any('{until}' in arg for arg in argv)):
            raise ValueError('invalid_provider_argv')
        if any(re.search(r'(?i)(?:token|password|passwd|secret|api[_-]?key|authorization|credential)', arg) for arg in argv):
            raise ValueError('provider_credentials_require_environment')
        if 'token_ref' in config or 'executable' in config:
            raise ValueError('invalid_provider_config')
    if kind not in {'railway', 'vercel'} and 'token_ref' in config:
        raise ValueError('invalid_provider_config')
    if config.get('token_env') and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', config['token_env']):
        raise ValueError('invalid_provider_token_env')
    if 'token_env' in config and 'token_ref' in config:
        raise ValueError('invalid_provider_config')
    if 'token_ref' in config:
        try:
            if not isinstance(config['token_ref'], str) or str(UUID(config['token_ref'])) != config['token_ref']:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid_provider_token_ref') from None
    if kind == 'vercel' and config['provider_environment'] not in {'production', 'preview'}:
        raise ValueError('invalid_provider_environment')
    for name, default, maximum in [('interval_seconds', 10, 300), ('window_seconds', 300, 300), ('timeout_seconds', 20, 60)]:
        item = config.setdefault(name, default)
        if type(item) is not int or not 1 <= item <= maximum:
            raise ValueError('invalid_provider_schedule')
    try:
        initial = timestamp(config.get('since', (_now() - timedelta(seconds=300)).isoformat()))
        if initial > _now():
            raise ValueError()
        config['since'] = initial.isoformat()
    except (ProviderError, ValueError):
        raise ValueError('invalid_provider_since') from None
    return config


class ProviderCollection:
    def __init__(self, store):
        self.store = store
        self.task = None
        self._poll_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        with store.connection(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS native_provider_sources (
                source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, config_json TEXT NOT NULL, binding_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL, cursor TEXT NOT NULL, window_seconds INTEGER NOT NULL,
                state TEXT NOT NULL, error_code TEXT, retry_at REAL NOT NULL DEFAULT 0,
                next_poll_at REAL NOT NULL DEFAULT 0, observed_events INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                generation INTEGER NOT NULL DEFAULT 1)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS native_provider_backoff (
                scope TEXT PRIMARY KEY, retry_at REAL NOT NULL)''')

    def _binding(self, connection, project_id, source_id, kind):
        row = connection.execute('SELECT kind FROM sources WHERE id=? AND project_id=?', (source_id, project_id)).fetchone()
        if row is None or row['kind'] != kind:
            raise ValueError('invalid_provider_binding')

    def _runtime(self):
        from .raw_capture import load_capture_policy
        policy = load_capture_policy(self.store.state_dir)
        runtime = self.store.rag_runtime
        if runtime is None and (policy['mode'] == 'retain_until_summarized' or (self.store.state_dir / 'rag.json').exists()):
            from .rag_runtime import NativeRAGRuntime
            runtime = self.store.rag_runtime = NativeRAGRuntime(self.store)
        if runtime is not None and hasattr(runtime, '_refresh_configuration'):
            runtime._refresh_configuration()
        if runtime is None or policy['mode'] != 'retain_until_summarized' and getattr(runtime, 'model_profile', None) is None:
            raise ValueError('provider_model_not_configured')
        return runtime

    def configure(self, project_id, source_id, kind, config):
        config = validate_config(kind, config)
        self._runtime()
        identity = {key: value for key, value in config.items() if key not in {
            'since', 'interval_seconds', 'window_seconds', 'timeout_seconds', 'token_env', 'token_ref'}}
        binding = hashlib.sha256(json.dumps([project_id, source_id, kind, identity], sort_keys=True).encode()).hexdigest()
        old_ref = None
        with self.store.connection(write=True) as connection:
            self._binding(connection, project_id, source_id, kind)
            previous = connection.execute('SELECT * FROM native_provider_sources WHERE source_id=?', (source_id,)).fetchone()
            if previous and previous['binding_hash'] != binding:
                raise ValueError('provider_binding_mismatch')
            if previous and previous['lease_until'] > time.time():
                raise ValueError('provider_poll_in_progress')
            if previous:
                old_ref = json.loads(previous['config_json']).get('token_ref')
                if old_ref and 'token_ref' not in config and 'token_env' not in config:
                    config['token_ref'] = old_ref
                connection.execute('''UPDATE native_provider_sources SET config_json=?, enabled=1,
                    window_seconds=?, state='configured',error_code=NULL,retry_at=0,next_poll_at=0,
                    failures=0,generation=generation+1 WHERE source_id=?''',
                    (json.dumps(config), config['window_seconds'], source_id))
            else:
                connection.execute('''INSERT INTO native_provider_sources
                    (source_id,project_id,kind,config_json,binding_hash,enabled,cursor,window_seconds,state)
                    VALUES(?,?,?,?,?,1,?,?,'configured')''',
                    (source_id, project_id, kind, json.dumps(config), binding, config['since'], config['window_seconds']))
        if old_ref and old_ref != config.get('token_ref'):
            from cli.secrets import delete_credential
            delete_credential(old_ref)
        self._wake.set()
        return self._one_status(source_id)

    def disable(self, project_id, source_id):
        reference = None
        with self.store.connection(write=True) as connection:
            row = connection.execute('SELECT * FROM native_provider_sources WHERE source_id=? AND project_id=?',
                                     (source_id, project_id)).fetchone()
            if row is None:
                raise ValueError('invalid_provider_binding')
            try:
                config = json.loads(row['config_json'])
                if not isinstance(config, dict):
                    config = {}
            except (ValueError, TypeError):
                config = {}
            reference = config.pop('token_ref', None)
            try:
                if str(UUID(reference)) != reference:
                    reference = None
            except (ValueError, TypeError, AttributeError):
                reference = None
            connection.execute('''UPDATE native_provider_sources SET enabled=0,state='disabled',
                config_json=?,generation=generation+1,error_code=NULL WHERE source_id=?''', (json.dumps(config), source_id))
        if reference:
            from cli.secrets import delete_credential
            delete_credential(reference)
        return self._one_status(source_id)

    def status(self, project_id=None):
        with self.store.connection() as connection:
            rows = connection.execute('''SELECT project_id,source_id,kind,enabled,cursor,window_seconds,state,
                error_code,retry_at,next_poll_at,observed_events FROM native_provider_sources'''
                + (' WHERE project_id=?' if project_id else '') + ' ORDER BY source_id',
                (project_id,) if project_id else ()).fetchall()
        return [{**dict(row), 'enabled': bool(row['enabled']), 'complete_coverage': False,
                 'running': self.task is not None and not self.task.done()} for row in rows]

    def _one_status(self, source_id):
        return next(row for row in self.status() if row['source_id'] == source_id)

    async def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name='native-provider-collection')
        return self.status()

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        return self.status()

    async def _run(self):
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # No arbitrary exception content can escape into daemon output.
                pass
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1)
            except TimeoutError:
                pass

    async def poll_once(self, source_id=None):
        if self._poll_lock.locked():
            return [{'state': 'busy', 'error_code': 'provider_poll_in_progress'}]
        async with self._poll_lock:
            with self.store.connection() as connection:
                rows = connection.execute('SELECT source_id FROM native_provider_sources WHERE enabled=1'
                    + (' AND source_id=?' if source_id else '') + ' ORDER BY source_id',
                    (source_id,) if source_id else ()).fetchall()
            results = []
            for row in rows:
                await self._poll_source(row['source_id'])
                results.append(self._one_status(row['source_id']))
            return results

    async def _poll_source(self, source_id):
        owner, now = str(uuid4()), time.time()
        with self.store.connection(write=True) as connection:
            row = connection.execute('SELECT * FROM native_provider_sources WHERE source_id=?', (source_id,)).fetchone()
            if not row or not row['enabled'] or max(row['retry_at'], row['next_poll_at'], row['lease_until']) > now:
                return
            try:
                config = validate_config(row['kind'], json.loads(row['config_json']))
                if type(row['window_seconds']) is not int or not 1 <= row['window_seconds'] <= MAX_WINDOW_SECONDS:
                    raise ValueError()
            except Exception:
                connection.execute("UPDATE native_provider_sources SET state='failed',error_code='invalid_provider_config',retry_at=? WHERE source_id=?",
                                   (now + 60, source_id))
                return
            scope = hashlib.sha256(json.dumps([row['kind'], config.get('provider_project'),
                config.get('provider_environment'), config.get('scope')], sort_keys=True).encode()).hexdigest()
            cooldown = connection.execute('SELECT retry_at FROM native_provider_backoff WHERE scope=?', (scope,)).fetchone()
            if cooldown and cooldown['retry_at'] > now:
                connection.execute("UPDATE native_provider_sources SET state='rate_limited',error_code='provider_rate_limited',retry_at=? WHERE source_id=?",
                                   (cooldown['retry_at'], source_id))
                return
            connection.execute('UPDATE native_provider_sources SET lease_owner=?,lease_until=? WHERE source_id=?',
                               (owner, now + 600, source_id))
        advanced, state, error, retry_at, count = None, 'failed', None, 0, 0
        window = row['window_seconds']
        try:
            with self.store.connection() as connection:
                self._binding(connection, row['project_id'], source_id, row['kind'])
            runtime = self._runtime()
            since = timestamp(row['cursor'])
            until = min(since + timedelta(seconds=window), _now() - timedelta(seconds=5))
            if until <= since:
                state = 'waiting'
                return
            token = None
            if config.get('token_ref'):
                from cli.secrets import read_credential
                try:
                    token = read_credential(config['token_ref'], purpose='native_provider',
                        project_id=row['project_id'], source_id=source_id, kind=row['kind'])
                except Exception:
                    raise ProviderError('provider_credential_unavailable') from None
                if not token:
                    raise ProviderError('provider_credential_unavailable')
            fetch_task = asyncio.create_task(asyncio.to_thread(fetch_window, row['kind'], config, source_id, since, until, token=token))
            try:
                events = await asyncio.shield(fetch_task)
            except asyncio.CancelledError:
                # Threads cannot be cancelled. Keep the lease until the bounded
                # subprocess exits so another manager cannot overlap this read.
                try:
                    await fetch_task
                except Exception:
                    pass
                raise
            # A disable while the read was in progress prevents native intake.
            with self.store.connection() as connection:
                current = connection.execute('SELECT enabled,generation FROM native_provider_sources WHERE source_id=?', (source_id,)).fetchone()
            if not current['enabled'] or current['generation'] != row['generation']:
                return
            if events:
                receipt = await runtime.ingest_async(row['project_id'], source_id, events)
                if (not isinstance(receipt, dict) or receipt.get('accepted') != len(events)
                        or receipt.get('state') not in {'durably_prepared', 'raw_pending'}):
                    raise ProviderError('provider_intake_not_durable')
            advanced, count = until.isoformat(), len(events)
            state = 'observed' if events else 'observed_empty'
        except asyncio.CancelledError:
            state, error = 'interrupted', 'provider_poll_interrupted'
            raise
        except Exception as failure:
            error = failure.code if isinstance(failure, ProviderError) else 'provider_collection_failed'
            from .raw_capture import SAFE_PROCESSING_FAILURES
            category = getattr(failure, 'category', None)
            if isinstance(category,str) and category in SAFE_PROCESSING_FAILURES:
                error = category
            if isinstance(failure, ValueError) and str(failure) in {'invalid_provider_binding', 'provider_model_not_configured'}:
                error = str(failure)
            state = 'rate_limited' if error == 'provider_rate_limited' else 'failed'
            if error == 'provider_window_capped':
                window = max(1, window // 2)
            delay = failure.retry_after if isinstance(failure, ProviderError) and error == 'provider_rate_limited' else min(300, 2 ** min(row['failures'] + 1, 8))
            retry_at = time.time() + delay
            if error == 'provider_rate_limited':
                with self.store.connection(write=True) as connection:
                    connection.execute('''INSERT INTO native_provider_backoff VALUES(?,?) ON CONFLICT(scope)
                        DO UPDATE SET retry_at=max(retry_at,excluded.retry_at)''', (scope, retry_at))
        finally:
            with self.store.connection(write=True) as connection:
                connection.execute('''UPDATE native_provider_sources SET cursor=coalesce(?,cursor),state=?,
                    error_code=?,retry_at=?,next_poll_at=?,window_seconds=?,observed_events=observed_events+?,
                    failures=? WHERE source_id=? AND generation=? AND enabled=1 AND lease_owner=? AND cursor=?''',
                    (advanced, state, error, retry_at, time.time() + config['interval_seconds'] if not error else 0,
                     window, count, row['failures'] + 1 if error else 0, source_id, row['generation'], owner, row['cursor']))
                connection.execute('''UPDATE native_provider_sources SET lease_owner=NULL,lease_until=0
                    WHERE source_id=? AND lease_owner=?''', (source_id, owner))
