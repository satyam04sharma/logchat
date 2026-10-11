"""Optional private offline replay: saved overflow proof plus synthetic child fit.

No native child-token claims are made. Configure both paths to immutable local
artifacts; no evidence files are written and no source data is printed.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
from datetime import timedelta

import pytest

from connectors.local import parse_push_events
from logchat.rag.builder import prepare_preserved_batch, event_key
from logchat.rag.contracts import SourceIdentity, TimeWindow
from logchat.rag.sections import (
    CATALOG_COMPACT_INSTRUCTION, SummaryConfig, _pack_compact_physical,
    _grouping_messages, _matching_model_event, records_for_chunk, section_batch, validate_refinement,
)
from logchat.rag.source_catalog import build_source_catalog
from pipeline.model_transport import SYSTEM_SAFETY_SUFFIX, model_json


def checked(condition, category):
    if not condition:
        raise AssertionError(category)


def hashed(value):
    return hashlib.sha256(model_json(value, default=str).encode()).hexdigest()


@pytest.mark.asyncio
async def test_saved_oversized_request_splits_under_explicit_offline_child_control(monkeypatch):
    bundle_path = os.environ.get('LOGCHAT_SAVED_INTAKE_REPLAY')
    producer_path = os.environ.get('LOGCHAT_SAVED_PRODUCER')
    if not bundle_path or not producer_path:
        pytest.skip('Private immutable replay inputs not configured')

    def denied(*args, **kwargs):
        raise AssertionError('offline_network_denied')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    spec = importlib.util.spec_from_file_location('saved_producer', producer_path)
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    base = Path(bundle_path)
    bundle = json.loads((base / 'submitted-fixtures.json').read_text())
    config = next(p for p in bundle['producers'] if p['app'] == 'auth')
    events = parse_push_events([producer.event(config, i) for i in range(400)],
                               source=config['source_id'], preserve_fields=True)
    identity = SourceIdentity('local-os-user', config['project_id'], config['environment_id'], config['source_id'])
    window = TimeWindow(min(e.ts for e in events), max(e.ts for e in events) + timedelta(microseconds=1))
    batch = prepare_preserved_batch(identity, events, window)
    sidecar = {event_key(identity, e.event_id): e for e in events}
    ledger = json.loads((base / 'native-proof/ledger-befc5574dee94bbdb485d4874a09b277.json').read_text())
    saved = [r for r in ledger['records'] if r['kind'] == 'generation' and r['sequence'] in (32, 33, 34, 35)]

    class Replay:
        local_only = preserve_content = True
        chat_model = 'mistral:7b'
        calls = 0
        overflow_seen = False

        async def generate(self, instruction, payload, schema):
            checked(self.calls < 3, 'unexpected_compaction_generation')
            row = saved[self.calls]
            self.calls += 1
            checked(all(hashed(v) == row[k] for k, v in (
                ('instruction_sha256', instruction), ('payload_sha256', payload), ('schema_sha256', schema))),
                'saved_grouping_request_mismatch')
            provider = json.loads((base / 'native-proof' / f'provider-generation-{row["epoch"]}-{row["sequence"]}.json').read_text())
            checked(hashed(provider) == row['provider_sha256'], 'saved_provider_hash_mismatch')
            return json.loads(provider['response'])

        async def preflight_generation(self, instruction, payload, schema):
            checked(self.calls == 3, 'compaction_before_plan')
            if hashed(payload) == saved[3]['payload_sha256']:
                checked(hashed(instruction) == saved[3]['instruction_sha256']
                        and hashed(schema) == saved[3]['schema_sha256'], 'saved_compact_request_mismatch')
                spelling = instruction + SYSTEM_SAFETY_SUFFIX + model_json(payload) + model_json(schema)
                checked(len(spelling) == len(spelling.encode()) == 18925, 'saved_spelling_mismatch')
                checked((len(payload['records']), len(payload['field_catalog']), len(payload['phrase_catalog']))
                        == (28, 33, 92), 'saved_catalog_counts_mismatch')
                native = saved[3]['native']
                checked(native['input_tokens'] == 7502 and native['output_reserve'] == 1200
                        and native['context_tokens'] == 8192 and not native['fits'], 'saved_native_proof_mismatch')
                self.overflow_seen = True
                return False
            # Mechanics-only oracle for all unmeasured children; never a token bound.
            return len(payload['records']) <= 14

    replay = Replay()
    sectioned = await section_batch(batch, replay, model_events=sidecar)
    views = _grouping_messages(sectioned, sidecar)
    from connectors.local import validated_normalization_origin
    from logchat.rag.sections import _source_fields, _json
    bodies = {}
    originals = [r for c in sectioned.chunks for r in records_for_chunk(c)]
    for record in originals:
        key = record['event_key']
        bodies[key] = {record['message'], views[key]}
        event = _matching_model_event(record, sidecar)
        origin = validated_normalization_origin(event)
        if origin is not None:
            bodies[key].add(event.message[:origin.prefix_chars])
    typed = {_json(v) for r in originals for v in _source_fields(r).values() if not isinstance(v, dict)}
    catalog = build_source_catalog(views, bodies, typed_values=typed)
    packed = await _pack_compact_physical(sectioned, replay, SummaryConfig(), views,
                                         source_catalog=catalog, protected_bodies=bodies)
    checked(replay.overflow_seen and replay.calls == 3, 'saved_overflow_not_replayed')
    retained = [r for c in packed.chunks for r in records_for_chunk(c)]
    checked(retained == originals and len(retained) == 400, 'record_conservation_failure')
    checked(packed.event_keys == sectioned.event_keys and packed.batch_id == sectioned.batch_id,
            'replay_identity_changed')
    validate_refinement(sectioned, packed)
