"""Synthetic reader controls only; no private replay or native acceptance."""
from copy import deepcopy
import hashlib

import pytest

from logchat.rag.compact_input import (
    COLUMNAR_INPUT_VERSION, COMPACT_INPUT_VERSION, project_compact_input,
)
from pipeline.model_transport import generation_body, model_json
from tests.test_compact_input_codec import complete_input
from tests.test_saved_compact_token_replay import (
    hashed, same_compact_values, saved_compact_request,
)


@pytest.mark.parametrize('version', [COMPACT_INPUT_VERSION, COLUMNAR_INPUT_VERSION])
def test_replay_compares_typed_values_without_rehashing_decoded_key_order(version):
    original = complete_input()
    shuffled = deepcopy(original)
    shuffled['records'] = [dict(reversed(list(row.items()))) for row in shuffled['records']]
    projected = project_compact_input(shuffled, version=version)
    assert hashed(shuffled) != hashed(original)
    assert same_compact_values(projected, original)
    assert same_compact_values(projected, project_compact_input(original))


@pytest.mark.parametrize('mutate', [
    lambda p: p['field_catalog'][1].update(value=0),  # False != 0
    lambda p: p['field_catalog'][2].update(value=0.0),  # 0 != 0.0
    lambda p: p['records'][2].update(duration_ms=1),  # 1.0 != 1
    lambda p: p['records'][1].update(duration_ms=0.0),  # -0.0 != 0.0
    lambda p: p['records'][0].pop('duration_ms'),  # null != missing
    lambda p: p['records'].reverse(),
    lambda p: p['field_catalog'][1]['event_refs'].reverse(),
    lambda p: p['phrase_catalog'].reverse(),
    lambda p: p['required_field_refs'].clear(),
])
@pytest.mark.parametrize('version', [COMPACT_INPUT_VERSION, COLUMNAR_INPUT_VERSION])
def test_replay_rejects_typed_or_ordered_value_changes(version, mutate):
    original = complete_input()
    changed = deepcopy(original)
    mutate(changed)
    assert not same_compact_values(project_compact_input(changed, version=version), original)


def request_fixture(tmp_path):
    payload, schema = complete_input(), {'type': 'object'}
    instruction = 'synthetic saved request'
    body = generation_body('synthetic-model', instruction, payload, schema)
    raw = model_json(body).encode()
    path = tmp_path / 'request.json'
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    row = {'request_sha256': digest, 'native': {'request_sha256': digest},
           'system_sha256': hashed(body['system']), 'instruction_sha256': hashed(instruction),
           'payload_sha256': hashed(payload), 'schema_sha256': hashed(schema)}
    return path, row, (instruction, payload, schema)


def test_saved_request_authenticates_original_bytes_and_preserves_payload_order(tmp_path):
    path, row, expected = request_fixture(tmp_path)
    before = path.read_bytes()
    result = saved_compact_request(path, row)
    assert tuple(map(model_json, result)) == tuple(map(model_json, expected))
    assert path.read_bytes() == before
    path.write_bytes(before + b' ')
    with pytest.raises(AssertionError, match='saved_compact_artifact_hash_mismatch'):
        saved_compact_request(path, row)


@pytest.mark.parametrize('key', [
    'request_sha256', 'native', 'system_sha256', 'instruction_sha256',
    'payload_sha256', 'schema_sha256',
])
def test_saved_request_rejects_each_mismatched_authenticated_hash(tmp_path, key):
    path, row, _ = request_fixture(tmp_path)
    if key == 'native':
        row[key]['request_sha256'] = '0' * 64
    else:
        row[key] = '0' * 64
    with pytest.raises(AssertionError, match='saved_compact_.*mismatch'):
        saved_compact_request(path, row)
