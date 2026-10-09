"""Fixture safety contracts. These do not substitute for browser/model acceptance."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('qa_native', ROOT / 'scripts/qa_native.py')
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def test_prepare_preserves_appended_fixture_and_separates_scopes(tmp_path):
    root = qa.run_root(tmp_path / 'owned')
    fixture = qa.prepare(root)
    assert len(fixture['projects']) == 5
    seen = set()
    for env, count in [('dev', 2), ('preview', 3), ('prod', 4)]:
        path = root / f'projects/auth app/{env}.ndjson'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(rows) == count
        assert all(env in row['email'] and row['error_code'].endswith(f'{env.upper()}_409') for row in rows)
        ids = {row['event_id'] for row in rows}
        assert not seen & ids
        seen |= ids
    path = root / 'projects/auth app/dev.ndjson'
    with path.open('a') as output:
        output.write('appended partial line')
    qa.prepare(root)
    assert path.read_text().endswith('appended partial line')
    assert not (root / 'state').exists()
    assert 'synthetic' in (root / 'qa-owner.json').read_text()


def test_foreign_nonempty_directory_and_checkout_are_rejected(tmp_path):
    (tmp_path / 'foreign').write_text('keep me')
    with pytest.raises(ValueError, match='not_empty'):
        qa.run_root(tmp_path)
    assert (tmp_path / 'foreign').read_text() == 'keep me'
    with pytest.raises(ValueError, match='outside_checkout'):
        qa.run_root(ROOT)


def test_wrong_ownership_marker_is_rejected_without_overwrite(tmp_path):
    marker = tmp_path / 'qa-owner.json'
    marker.write_text('{"owner":"foreign"}')
    with pytest.raises(ValueError, match='not_owned'):
        qa.run_root(tmp_path)
    assert json.loads(marker.read_text())['owner'] == 'foreign'


def test_fixture_environment_is_explicit_and_does_not_mutate_parent(tmp_path, monkeypatch):
    monkeypatch.setenv('LOGCHAT_CAPTURE_HOST', '1')
    import os
    environment = qa.environment(tmp_path)
    assert environment['LOGCHAT_CAPTURE_HOST'] == '0'
    assert environment['LOGCHAT_SECRETS_DIR'] == str(tmp_path / 'credentials')
    assert os.environ['LOGCHAT_CAPTURE_HOST'] == '1'


def test_review_failure_stops_owned_service_and_restores_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import httpx
    import os
    root = qa.run_root(tmp_path / 'run')
    qa.prepare(root)
    calls = []
    stopped = []
    monkeypatch.setenv('LOGCHAT_CAPTURE_HOST', 'original')
    monkeypatch.setattr('logchat.local.lifecycle.managed_process', lambda path: (None, None))
    monkeypatch.setattr('logchat.local.lifecycle.control_token', lambda path: 'synthetic-private-token')
    monkeypatch.setattr('logchat.local.lifecycle.stop', lambda path: stopped.append(path))
    monkeypatch.setattr(qa, 'cli', lambda root, *args: calls.append(args) or SimpleNamespace(returncode=0))
    def response(request):
        return httpx.Response(503, json={'detail': 'synthetic-private-token'})
    client_class = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(response)))
    with pytest.raises(httpx.HTTPStatusError):
        qa.serve(root)
    assert [args[0] for args in calls] == ['install', 'start']
    assert stopped == [root / 'state']
    assert os.environ['LOGCHAT_CAPTURE_HOST'] == 'original'


def test_review_refuses_to_stop_an_already_running_instance(tmp_path, monkeypatch):
    root = qa.run_root(tmp_path / 'run')
    stopped = []
    monkeypatch.setattr('logchat.local.lifecycle.managed_process', lambda path: (object(), {}))
    monkeypatch.setattr('logchat.local.lifecycle.stop', lambda path: stopped.append(path))
    with pytest.raises(RuntimeError, match='already_running'):
        qa.serve(root)
    assert not stopped


@pytest.mark.parametrize('failure', ['start', 'consumer'])
def test_inventory_container_is_restricted_and_removed_on_failure(monkeypatch, failure):
    from types import SimpleNamespace
    calls = []
    removed = False
    rows = [{'event_id': 'inventory-stdout'}, {'event_id': 'inventory-stderr'}]
    def docker(argv, **kwargs):
        nonlocal removed
        calls.append(argv)
        operation = argv[1]
        if operation == 'create':
            return SimpleNamespace(returncode=0, stdout='owned-container-id\n')
        if operation == 'start':
            return SimpleNamespace(returncode=int(failure == 'start'), stdout='inventory-stdout', stderr='inventory-stderr')
        if operation == 'rm':
            removed = True
            return SimpleNamespace(returncode=0)
        assert operation == 'inspect'
        return SimpleNamespace(returncode=int(removed), stdout=json.dumps([{
            'Mounts': [], 'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True,
            'Privileged': False, 'CapAdd': None, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges']}}]))
    monkeypatch.setattr(qa.subprocess, 'run', docker)
    with pytest.raises((AssertionError, RuntimeError)):
        with qa.inventory_container('logchat-qa-unit', rows):
            raise RuntimeError('consumer_failed')
    create = calls[0]
    assert create[create.index('--network') + 1] == 'none'
    assert create[create.index('--pull') + 1] == 'never'
    assert create[create.index('--cap-drop') + 1] == 'ALL'
    assert '--read-only' in create and '--security-opt' in create
    assert not {'--mount', '-v', '--volume', '--privileged', '--cap-add'} & set(create)
    assert removed and calls[-2:] == [['docker', 'rm', '--force', 'owned-container-id'],
                                     ['docker', 'inspect', 'owned-container-id']]


def test_inventory_container_does_not_remove_foreign_or_failed_create(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(qa.subprocess, 'run', lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match='name_required'):
        with qa.inventory_container('foreign', []):
            pass
    assert not calls
    with pytest.raises(RuntimeError, match='create_failed'):
        with qa.inventory_container('logchat-qa-unit', []):
            pass
    assert len(calls) == 1 and calls[0][1] == 'create'


def test_console_busy_retry_keeps_identical_payload_and_rejects_model_failure(monkeypatch):
    import httpx
    spec = importlib.util.spec_from_file_location('qa_browser', ROOT / 'scripts/qa_browser.py')
    browser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser)
    monkeypatch.setattr(browser.time, 'sleep', lambda seconds: None)
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, headers={'X-Logchat-Processing-Category': 'intake_busy_retry_required'})
        return httpx.Response(200, json={'accepted': 1})
    body = {'events': [{'event_id': 'stable', 'message': 'synthetic'}]}
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(respond)) as client:
        assert browser.push_with_busy_retry(client, '/events', body, 'fake') == ({'accepted': 1}, 1)
    assert requests == [body, body]
    requests.clear()
    def model_failure(request):
        requests.append(request)
        return httpx.Response(503, headers={'X-Logchat-Processing-Category': 'compact_invalid_output'})
    with httpx.Client(base_url='http://127.0.0.1', transport=httpx.MockTransport(model_failure)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            browser.push_with_busy_retry(client, '/events', body, 'fake')
    assert len(requests) == 1


@pytest.mark.parametrize('fail', [False, True])
def test_independent_file_producer_acknowledges_fragments_and_stops(tmp_path, fail):
    path = tmp_path / 'app.ndjson'
    process = None
    try:
        with qa.file_producer(path) as (process, append):
            append('{"message":')
            append('"partial"}')
            assert path.read_text() == '{"message":"partial"}'
            assert process.poll() is None
            append('\n')
            assert path.read_text().endswith('\n')
            if fail:
                raise RuntimeError('consumer_failed')
    except RuntimeError:
        assert fail
    assert process is not None and process.poll() is not None
    assert process.stdin.closed
