"""Finite native CLI windows; untrusted output stays in bounded memory.

CLI flags: docs.docker.com/reference/cli/docker/container/logs/,
docs.railway.com/cli/logs, vercel.com/docs/cli/logs. Vercel's official
vercel-labs/agent-skills vercel-cli-with-tokens documents VERCEL_TOKEN.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import selectors
import signal
import subprocess
import time

from connectors.local import parse_event

MAX_EVENTS = 400
MAX_OUTPUT_BYTES = 512 * 1024


class ProviderError(Exception):
    def __init__(self, code, retry_after=60):
        super().__init__(code)
        self.code = code
        self.retry_after = max(1, min(86400, int(retry_after)))


def timestamp(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise ValueError()
            # Vercel's created timestamps are Unix milliseconds.
            parsed = datetime.fromtimestamp(value / 1000 if abs(value) > 10**11 else value, timezone.utc)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        else:
            raise ValueError()
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ProviderError('provider_invalid_event') from None


def command(kind, config, since, until):
    left, right = since.isoformat(), until.isoformat()
    executable = config.get('executable', kind)
    if kind == 'docker':
        return [executable, 'logs', '--timestamps', '--since', left, '--until', right, config['container']]
    if kind == 'railway':
        return [executable, 'logs', '--json', '--lines', '1000', '--since', left, '--until', right,
                '--project', config['provider_project'], '--environment', config['provider_environment'],
                '--service', config['service']]
    if kind == 'vercel':
        result = [executable, 'logs', '--json', '--expand', '--no-branch', '--limit', '1000',
                  '--since', left, '--until', right, '--project', config['provider_project'],
                  '--environment', config['provider_environment']]
        if config.get('scope'):
            result += ['--scope', config['scope']]
        return result
    return [arg.replace('{since}', left).replace('{until}', right) for arg in config['argv']]


def bounded_run(argv, *, cwd=None, env=None, timeout=20, max_bytes=MAX_OUTPUT_BYTES):
    """Drain both pipes incrementally, killing the process group on any bound."""
    if os.name != 'posix':
        raise ProviderError('provider_platform_unsupported')
    process = None
    selector = selectors.DefaultSelector()
    streams = [bytearray(), bytearray()]
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd, env=env, shell=False,
                                   start_new_session=True)
        for index, pipe in enumerate((process.stdout, process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, index)
        size = 0
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError('provider_timeout')
            for key, _ in selector.select(min(remaining, .1)):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                size += len(data)
                if size > max_bytes:
                    raise ProviderError('provider_window_capped')
                streams[key.data].extend(data)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError('provider_timeout')
        process.wait(timeout=remaining)
        if process.returncode:
            diagnostic = bytes(streams[0] + streams[1]).lower()
            if any(term in diagnostic for term in (b'rate limit', b'ratelimit', b'too many requests', b'429')):
                match = re.search(rb'(?:retry.after\s*[:=]?\s*)(\d+)', diagnostic)
                raise ProviderError('provider_rate_limited', int(match[1]) if match else 300)
            raise ProviderError('provider_cli_failed')
        return bytes(streams[0]), bytes(streams[1])
    except ProviderError:
        raise
    except subprocess.TimeoutExpired:
        raise ProviderError('provider_timeout') from None
    except (OSError, ValueError):
        raise ProviderError('provider_cli_unavailable') from None
    finally:
        selector.close()
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            for pipe in (process.stdout, process.stderr):
                pipe.close()


def normalize(kind, output, source_id, since, until, *, service=None):
    """Preserve fields and multiplicity; deterministic content/ordinal identities.

IDs do not contain window boundaries. This protects retries after durable intake
commits but before the observation cursor commits, including identical records.
"""
    events, duplicates = [], Counter()
    try:
        lines = output.decode('utf-8', errors='strict').splitlines()
        if len(lines) > MAX_EVENTS:
            raise ProviderError('provider_window_capped')
        for line in lines:
            if not line.strip():
                continue
            if kind == 'docker':
                stamp, body = line.split(' ', 1)
                try:
                    value = json.loads(body)
                except json.JSONDecodeError:
                    value = {'message': body}
                if not isinstance(value, dict):
                    value = {'message': body}
                # Retain any application timestamp separately from Docker's.
                if 'timestamp' in value:
                    value['application_timestamp'] = value['timestamp']
                value['timestamp'] = stamp
            else:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError()
            stamp = next((value[key] for key in ('timestamp', 'ts', 'time', '@timestamp', 'created')
                          if key in value), None)
            parsed = timestamp(stamp)
            if not since <= parsed < until:
                continue
            canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            digest = hashlib.sha256((source_id + '\0' + canonical).encode()).hexdigest()
            ordinal = duplicates[digest]
            duplicates[digest] += 1
            prepared = dict(value)
            prepared['timestamp'] = parsed.isoformat()
            message = next((value[key] for key in ('message', 'msg', 'event') if key in value), None)
            if message is None or isinstance(message, str) and not message.strip():
                prepared['message'] = f'[{kind} omitted text]'
            if service and not prepared.get('service'):
                prepared['service'] = service
            if 'http_status' in prepared and 'request_status' not in prepared:
                prepared['request_status'] = prepared['http_status']
            event = parse_event(prepared, source=source_id, preserve_fields=True)
            events.append(replace(event, event_id=f'provider:{digest}:{ordinal}'))
        return events
    except ProviderError:
        raise
    except (UnicodeError, ValueError, TypeError, OverflowError, OSError):
        raise ProviderError('provider_invalid_event') from None


def fetch_window(kind, config, source_id, since, until, *, token=None):
    environment = dict(os.environ)
    name = config.get('token_env')
    if name:
        token = environment.get(name)
        if not token:
            raise ProviderError('provider_credential_unavailable')
    if token is not None and kind == 'railway':
        token_key = 'RAILWAY_API_TOKEN' if name == 'RAILWAY_API_TOKEN' else 'RAILWAY_TOKEN'
        environment.pop('RAILWAY_TOKEN' if token_key == 'RAILWAY_API_TOKEN' else 'RAILWAY_API_TOKEN', None)
        environment[token_key] = token
    elif token is not None and kind == 'vercel':
        environment['VERCEL_TOKEN'] = token
    stdout, stderr = bounded_run(command(kind, config, since, until), cwd=config.get('cwd'),
                                 env=environment, timeout=config.get('timeout_seconds', 20))
    # Docker emits the container's stderr on the CLI stderr pipe on success.
    output = stdout + (b'\n' + stderr if kind == 'docker' and stderr else b'')
    return normalize(kind, output, source_id, since, until, service=config.get('service'))
