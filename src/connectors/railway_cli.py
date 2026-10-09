"""Read-only Railway CLI adapter. Credentials remain owned by the user's CLI.

Provider output is captured in memory, never echoed or written to a log file.
Finite windows are split if a page reaches its cap; incomplete windows fail closed.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import asyncio
import json
import re
import subprocess

from connectors.base import ConnectorError
from connectors.local import parse_event


# Keep placeholder terms outside native memory's operational vocabulary: absent
# text must not manufacture an availability failure in lexical retrieval.
EMPTY_MESSAGE_PLACEHOLDER = '[Railway omitted text]'


def _normalize_message(value):
    """Match parse_event's alias precedence without coercing non-text values."""
    key = next((key for key in ('message', 'msg', 'event') if key in value), None)
    message = value[key] if key else None
    reason = ('missing' if key is None else 'null' if message is None else
              'empty' if isinstance(message, str) and not message.strip() else None)
    if reason:
        value['message'] = EMPTY_MESSAGE_PLACEHOLDER
    return reason


def _provider_timestamp(value):
    """Require an explicit instant, using the push parser's alias precedence."""
    timestamp = next((value[key] for key in ('timestamp', 'ts', 'time', '@timestamp')
                      if key in value), None)
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    elif isinstance(timestamp, str):
        parsed = datetime.fromisoformat(timestamp.strip().replace('Z', '+00:00'))
    else:
        raise ValueError()
    if parsed.utcoffset() is None:
        raise ValueError()
    return parsed.astimezone(timezone.utc)


class RailwayRateLimited(ConnectorError):
    def __init__(self, retry_after=300):
        super().__init__('railway_cli_rate_limited')
        self.retry_after = max(60, min(int(retry_after), 86400))


class RailwayCollectionDeferred(ConnectorError):
    def __init__(self, retry_at):
        super().__init__('railway_cli_rate_limited')
        self.retry_at = retry_at


class RailwayCLIConnector:
    def __init__(self, config, credential=None, *, check_cooldown=None):
        self.service = config['service']
        self.prefix = list(config.get('command_prefix') or ['railway'])
        self.limit = 1000
        self.message_normalization_counts = {'missing': 0, 'null': 0, 'empty': 0}
        self.check_cooldown = check_cooldown or (lambda: None)
        if not self.prefix or not all(isinstance(value, str) and value for value in self.prefix):
            raise ConnectorError('invalid_cli_prefix')

    async def probe(self, since, until):
        return None  # CLI caps and delayed arrival prevent a completeness claim.

    def _read(self, since, until):
        command = self.prefix + ['logs', '--service', self.service, '--json', '--lines', str(self.limit),
                                 '--since', since.isoformat(), '--until', until.isoformat()]
        try:
            result = subprocess.run(command, capture_output=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            raise ConnectorError('railway_cli_unavailable') from None
        if result.returncode:
            error = result.stderr.lower()
            if any(term in error for term in (b'ratelimit', b'rate limit', b'too many requests')):
                match = re.search(rb'(\d+)\s*(seconds?|minutes?|hours?)', error)
                seconds = int(match[1]) * (3600 if match[2].startswith(b'hour') else 60 if match[2].startswith(b'minute') else 1) if match else 300
                raise RailwayRateLimited(seconds)
            raise ConnectorError('railway_cli_failed')
        if len(result.stdout) > 16 * 1024 * 1024:
            raise ConnectorError('railway_cli_output_limit')
        lines = result.stdout.splitlines()
        if len(lines) >= self.limit:
            raise ConnectorError('railway_cli_window_capped')
        events = []
        normalized = dict.fromkeys(self.message_normalization_counts, 0)
        for line in lines:
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError()
                value['timestamp'] = _provider_timestamp(value).isoformat()
                value['service'] = self.service
                if 'http_status' in value:
                    value['request_status'] = value['http_status']
                reason = _normalize_message(value)
                event = parse_event(value, source='railway')
                # Half-open windows prevent duplicates when providers include both boundaries.
                if since <= event.ts < until:
                    events.append(replace(event, service=self.service))
                    if reason:
                        normalized[reason] += 1
            except (ValueError, TypeError, OverflowError, OSError):
                raise ConnectorError('railway_cli_invalid_event') from None
        # Only validated, in-window records from a completed page contribute.
        for reason, count in normalized.items():
            self.message_normalization_counts[reason] += count
        return events

    async def fetch(self, since: datetime, until: datetime):
        if not since.tzinfo or not until.tzinfo or since >= until:
            raise ConnectorError('invalid_collection_window')
        self.message_normalization_counts = dict.fromkeys(self.message_normalization_counts, 0)
        pending = [(since, until)]
        windows = 0
        while pending:
            self.check_cooldown()
            left, right = pending.pop()
            windows += 1
            if windows > 127:
                raise ConnectorError('railway_cli_window_capped')
            try:
                events = await asyncio.to_thread(self._read, left, right)
            except ConnectorError as error:
                if str(error) != 'railway_cli_window_capped' or right-left <= timedelta(seconds=1):
                    raise
                middle = left + (right-left)/2
                pending.extend([(middle, right), (left, middle)])
                continue
            # A sibling may have reported Retry-After while this CLI call ran.
            self.check_cooldown()
            for event in events:
                yield event
