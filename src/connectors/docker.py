"""Docker Engine logs via an opt-in, GET-only internal socket proxy."""
import hashlib
import json
import math
import os
import re
import struct
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from connectors.base import ConnectorError
from pipeline.types import LogEvent

MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 10000


def timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
    value = re.sub(r'(\.\d{6})\d+(?=Z|[+-])', r'\1', str(value))
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def normalize(message, ts, source, service, event_id=None):
    try:
        record = json.loads(message)
        if not isinstance(record, dict): record = {}
    except ValueError:
        record = {}
    text = str(record.get('message', record.get('msg', message)))
    level = str(record.get('level', 'error' if re.search(r'\b(error|exception|failed|fatal)\b', text, re.I) else 'info')).lower()
    duration = record.get('duration_ms')
    if not isinstance(duration, (int,float)) or isinstance(duration,bool) or not math.isfinite(duration) or duration < 0: duration = None
    status = record.get('status', record.get('status_code'))
    if not isinstance(status, int) or isinstance(status,bool) or not 100 <= status <= 599: status = None
    return LogEvent(event_id=event_id or hashlib.sha256((ts.isoformat() + message).encode()).hexdigest(), ts=ts,
        source=source, service=str(record.get('service', service)), level=level, message=text,
        fingerprint=str(record.get('fingerprint', re.sub(r'\b\d+\b', '#', text)[:200])),
        release=str(record['release']) if record.get('release') else None, duration_ms=duration, request_status=status)


class DockerConnector:
    def __init__(self, config, credential=None, transport=None):
        self.container = config['container']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', self.container): raise ConnectorError('invalid_container')
        self.service = config.get('service', self.container)
        self.base_url = os.getenv('DOCKER_PROXY_URL', 'http://docker-proxy:2375')
        self.transport = transport

    async def probe(self, since, until):
        # Engine does not expose a reliable count API. Do not fetch raw logs twice.
        try:
            async with httpx.AsyncClient(trust_env=False, transport=self.transport, timeout=10) as client:
                response = await client.get(self.base_url + '/containers/' + quote(self.container) + '/json')
                response.raise_for_status()
            return None
        except Exception:
            raise ConnectorError('source_unavailable') from None

    async def fetch(self, since, until):
        params = {'stdout':'1','stderr':'1','timestamps':'1','since':str(since.timestamp()),'until':str(until.timestamp()),'follow':'0'}
        try:
            async with httpx.AsyncClient(trust_env=False, transport=self.transport, timeout=30) as client:
                async with client.stream('GET', self.base_url + '/containers/' + quote(self.container) + '/logs', params=params) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for piece in response.aiter_bytes():
                        data.extend(piece)
                        if len(data) > MAX_BYTES: raise ConnectorError('batch_limit_exceeded')
        except ConnectorError: raise
        except Exception: raise ConnectorError('fetch_failed') from None
        # Non-TTY streams are multiplexed; TTY streams are plain text.
        payload = bytearray()
        if len(data) >= 8 and data[0] in (0,1,2) and data[1:4] == b'\0\0\0':
            offset = 0
            while offset < len(data):
                if offset + 8 > len(data): raise ConnectorError('incomplete_docker_frame')
                size = struct.unpack('>I', data[offset+4:offset+8])[0]
                if offset+8+size > len(data): raise ConnectorError('incomplete_docker_frame')
                payload.extend(data[offset+8:offset+8+size]); offset += 8+size
        else: payload=data
        count = 0
        occurrences = {}
        for line in payload.decode('utf-8', errors='replace').splitlines():
            date, sep, message = line.partition(' ')
            if not sep: raise ConnectorError('missing_event_timestamp')
            try: ts = timestamp(date)
            except ValueError: raise ConnectorError('invalid_event_timestamp') from None
            if since <= ts < until:
                count += 1
                if count > MAX_EVENTS: raise ConnectorError('batch_limit_exceeded')
                key = hashlib.sha256(line.encode()).hexdigest()
                occurrences[key] = occurrences.get(key, 0)+1
                yield normalize(message, ts, 'docker', self.service, key+':'+str(occurrences[key]))
