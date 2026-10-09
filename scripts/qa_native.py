"""Isolated synthetic QA. Reports never contain CLI output, credentials or provider payloads."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'examples/qa/demo.py'
spec = importlib.util.spec_from_file_location('qa_demo', DEMO)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
MARKER = 'logchat-synthetic-qa-v1'


def run_root(value):
    path = Path(value).expanduser().resolve()
    if path == ROOT or ROOT in path.parents or path == Path.home() or path == Path('/'):
        raise ValueError('qa_output_must_be_outside_checkout')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = path / 'qa-owner.json'
    if marker.exists():
        if json.loads(marker.read_text()).get('owner') != MARKER:
            raise ValueError('qa_directory_not_owned')
    elif any(path.iterdir()):
        # An explicitly selected run root may contain the dedicated environment only.
        if {p.name for p in path.iterdir()} - {'venv', 'evidence'}:
            raise ValueError('qa_directory_not_empty')
    marker.write_text(json.dumps({'owner': MARKER}))
    marker.chmod(0o600)
    return path


def prepare(root):
    projects = root / 'projects'
    for name in ['auth app', 'payments app', 'console app', 'inventory app', 'custom cli app']:
        (projects / name).mkdir(parents=True, exist_ok=True)
    for environment in ['dev', 'preview', 'prod']:
        path = projects / 'auth app' / f'{environment}.ndjson'
        if not path.exists():
            path.write_text(''.join(json.dumps(row) + '\n' for row in demo.events(environment)))
    shutil.copyfile(ROOT / 'examples/qa/console.html', projects / 'console app' / 'index.html')
    manifest = {'owner': MARKER, 'url': 'http://127.0.0.1:18940',
                'projects': [str(p) for p in sorted(projects.iterdir())],
                'limitations': ['Synthetic data only', 'Cloud commands mocked; no live cloud verification',
                                'Docker not run by prepare; requires --docker-walkthrough', 'Preparation does not prove retrieval']}
    (root / 'fixture.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def environment(root):
    return dict(os.environ, LOGCHAT_CAPTURE_HOST='0', LOGCHAT_SECRETS_DIR=str(root / 'credentials'),
                LOGCHAT_LOCAL_DIR=str(root / 'state'), NO_COLOR='1', TERM='dumb')


def cli(root, *args, timeout=180):
    executable = Path(sys.executable).parent / 'logchat'
    if not executable.is_file():
        raise RuntimeError('installed_logchat_executable_missing')
    return subprocess.run([str(executable), *args], cwd=root, env=environment(root),
                          capture_output=True, text=True, timeout=timeout)


def checks(root):
    from connectors.provider_cli import ProviderError, bounded_run, normalize
    since = datetime(2026, 10, 4, tzinfo=timezone.utc)
    until = since + timedelta(minutes=5)
    results = []
    def check(identifier, label, fn):
        try:
            fn()
            results.append({'id': identifier, 'check': label, 'status': 'passed'})
        except Exception as error:
            results.append({'id': identifier, 'check': label, 'status': 'failed',
                            'error_category': type(error).__name__})
    def help_check(args, expected):
        result = cli(root, *args, '--help', timeout=30)
        assert result.returncode == 0 and expected in result.stdout
    for args, expected in [((), 'install'), (('install',), '--model'), (('local',), 'connect-provider'),
                           (('local', 'connect'), '--from-start'), (('local', 'connect-provider'), '--new-source'),
                           (('settings',), 'capture'), (('settings', 'capture'), '--retention-hours'),
                           (('settings', 'ui'), '--no-environments'), (('local', 'ask'), '--json')]:
        check('C02', 'installed help: ' + ' '.join(args), lambda a=args, e=expected: help_check(a, e))
    def fixture_check(kind, case):
        argv = [sys.executable, str(DEMO), 'cloud-mock',
                '--case', case, '--since', since.isoformat(), '--until', until.isoformat()]
        expected = {'auth': 'provider_cli_failed', 'rate': 'provider_rate_limited',
                    'cap': 'provider_window_capped', 'malformed': 'provider_invalid_event',
                    'naive': 'provider_invalid_event'}.get(case)
        try:
            output, _ = bounded_run(argv, timeout=5)
            rows = normalize(kind, output, 'fixture', since, until)
            assert len(rows) == 2 and 'dev-1@example.test' in rows[0].message
            assert expected is None
        except ProviderError as error:
            assert expected and str(error) == expected
            assert 'synthetic-provider-canary' not in str(error)
    for kind in ['railway', 'vercel', 'cli']:
        for case in ['ok', 'auth', 'rate', 'cap', 'malformed', 'naive']:
            check('C14' if kind == 'cli' else 'C15', f'{kind} mocked command: {case}',
                  lambda k=kind, c=case: fixture_check(k, c))
    def payments_check():
        result = subprocess.run([sys.executable, str(DEMO), 'payments'], capture_output=True, text=True, timeout=5)
        assert result.returncode == 7
        assert json.loads(result.stdout)['event_id'] == 'PAY-1'
        assert json.loads(result.stderr)['event_id'] == 'PAY-2'
    check('C11', 'payments fixture streams and exit (not ingestion)', payments_check)
    report = {'mode': 'installed CLI and synthetic adapter command contracts; no model/browser verification',
              'checks': results, 'passed': sum(r['status'] == 'passed' for r in results),
              'failed': sum(r['status'] == 'failed' for r in results)}
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / 'contracts.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'passed': report['passed'], 'failed': report['failed'], 'report': str(evidence / 'contracts.json')}))
    return report['failed'] == 0


def payments_walkthrough(root, client):
    """Required installed wrapped capture gate, with real indexed scoped evidence."""
    import tomllib
    from uuid import uuid4
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    cases = []
    def request(method, path, body=None):
        response = client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()
    def check(label, action):
        try:
            action()
            cases.append({'check': label, 'status': 'passed'})
        except Exception as error:
            cases.append({'check': label, 'status': 'failed', 'error_category': type(error).__name__})
    project_path = root / 'projects/payments app'
    run_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    baseline = 0
    binding_path = project_path / '.logchat/local.toml'
    if binding_path.exists():
        prior = tomllib.loads(binding_path.read_text())
        before = request('GET', f"/projects/{prior['project_id']}/search?environment_id={prior['environment_id']}")
        assert not before['limit_reached']
        baseline = sum(m['metrics']['event_count'] for m in before['evidence']
                       if m['identity']['source_id'] == prior['source_id'])
    def capture():
        started = time.monotonic()
        result = cli(root, 'local', 'connect', '--project', str(project_path),
                     '--state-dir', str(root / 'state'), '--logchat-port', '18940',
                     '--', sys.executable, str(DEMO), 'payments', '--run-id', run_id, timeout=660)
        categories = [line.removeprefix('Logchat capture: ') for line in result.stderr.splitlines()
                      if line.startswith('Logchat capture: ')]
        (evidence / 'payments-delivery.json').write_text(json.dumps({
            'exit_code': result.returncode, 'seconds': round(time.monotonic() - started, 2),
            'delivery_categories': categories, 'incomplete': 'Coverage is incomplete' in result.stderr,
        }, indent=2))
        assert result.returncode == 7
        assert 'PAY-1' in result.stdout and 'PAY-2' in result.stderr
        assert not categories and 'Coverage is incomplete' not in result.stderr
    check('C11 installed wrapped stdout/stderr, complete delivery and exit 7', capture)
    config = tomllib.loads((project_path / '.logchat/local.toml').read_text())
    project_id, source_id = config['project_id'], config['source_id']
    env_id = next(e['id'] for e in request('GET', f'/projects/{project_id}/environments') if e['name'] == 'dev')
    def indexed():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = request('GET', f'/projects/{project_id}/search?environment_id={env_id}')
            memories = [m for m in result['evidence'] if m['identity']['source_id'] == source_id]
            if sum(m['metrics']['event_count'] for m in memories) >= baseline + 2:
                (evidence / 'payments-indexed.json').write_text(json.dumps(result, indent=2))
                assert not result['limit_reached']
                assert sum(m['metrics']['event_count'] for m in memories) == baseline + 2
                assert all('PAY-' + str(number) + '-' + run_id in json.dumps(memories) for number in [1, 2])
                return
            time.sleep(1)
        raise AssertionError('payments_not_indexed')
    check('C11 both wrapped events indexed by shared semantic core', indexed)
    def scoped():
        result = request('POST', f'/projects/{project_id}/ask', {
            'question': 'PAY_DECLINED_42', 'environment_ids': [env_id], 'service': 'payments', 'timezone': 'UTC',
            'start': started_at, 'end': datetime.now(timezone.utc).isoformat()})
        (evidence / 'payments-context.json').write_text(json.dumps(result, indent=2))
        selected = result.get('evidence', [])
        assert selected
        assert all(m['project_id'] == project_id and m['environment_id'] == env_id
                   and m['source_id'] == source_id for m in selected)
        text = json.dumps(selected)
        assert all(value in text for value in ['PAY-1-' + run_id, 'PAY-2-' + run_id, 'PAY_DECLINED_42'])
        assert 'LIB_AUTH_' not in text and 'GlyphIndexMismatch' not in text
        assert sum(m['event_count'] for m in selected) == 2
        assert sum(m['duration_sum_ms'] for m in selected) == 300
    check('C21/C22 payments scoped exact identifiers and measured totals', scoped)
    report = {'checks': cases, 'passed': sum(c['status'] == 'passed' for c in cases),
              'failed': sum(c['status'] == 'failed' for c in cases)}
    (evidence / 'payments-walkthrough.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return report['failed'] == 0


def custom_walkthrough(root, client):
    """Installed finite CLI capture, distinct identities and scoped shared-core evidence."""
    import tomllib
    from uuid import uuid4
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    run_id = uuid4().hex
    cases, bindings = [], []
    project = root / 'projects/custom cli app' / run_id
    other = root / 'projects/custom cli app' / (run_id + '-other')
    for path in [project, other]:
        path.mkdir()
    executable = project / 'finite-logs'
    executable.write_text(f'#!{sys.executable}\n' + '''import argparse, json
from datetime import datetime
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--since', required=True)
p.add_argument('--until', required=True)
p.add_argument('--data', required=True)
a = p.parse_args()
left, right = datetime.fromisoformat(a.since), datetime.fromisoformat(a.until)
for line in Path(a.data).read_text().splitlines():
    row = json.loads(line)
    if left <= datetime.fromisoformat(row['timestamp']) < right:
        print(json.dumps(row), flush=True)
''')
    executable.chmod(0o700)
    stamp = datetime.now(timezone.utc) - timedelta(seconds=15)
    since = (stamp - timedelta(seconds=1)).isoformat()

    def request(method, path, body=None):
        response = client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()

    def check(label, action):
        try:
            action()
            cases.append({'check': label, 'status': 'passed'})
        except Exception as error:
            cases.append({'check': label, 'status': 'failed', 'error_category': type(error).__name__})

    def configure(path, number, new=False):
        data = project / f'events-{number}.ndjson'
        marker = f'CUSTOM_{number}_{run_id}'
        row = dict(timestamp=stamp.isoformat(), event_id=marker, service=f'custom-{number}',
                   level='error', message=f'Custom lookup refused {marker}',
                   error_code=marker, email=f'custom-{number}@example.test', duration_ms=number * 101)
        data.write_text(json.dumps(row) + '\n')
        config = project / f'adapter-{number}.json'
        config.write_text(json.dumps({'argv': [str(executable), '--data', str(data),
                                               '--since', '{since}', '--until', '{until}'],
                                     'since': since, 'interval_seconds': 1}))
        args = ['local', 'connect-provider', 'cli', '--project', str(path),
                '--state-dir', str(root / 'state'), '--logchat-port', '18940',
                '--config-file', str(config)]
        result = cli(root, *args, *(['--new-source'] if new else []), timeout=360)
        assert result.returncode == 0
        binding = tomllib.loads((path / '.logchat/local.toml').read_text())
        # Evidence deliberately excludes credential references and CLI output.
        item = {key: binding[key] for key in ['project_id', 'source_id', 'environment_id']}
        item.update(marker=marker, service=f'custom-{number}', number=number)
        bindings.append(item)
        return args, item

    def memories(item):
        result = request('GET', f"/projects/{item['project_id']}/search?environment_id={item['environment_id']}")
        assert not result['limit_reached']
        return [m for m in result['evidence'] if m['identity']['source_id'] == item['source_id']]

    def wait_indexed(item):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            rows = memories(item)
            if rows:
                assert sum(m['metrics']['event_count'] for m in rows) == 1
                assert item['marker'] in json.dumps(rows)
                return rows
            time.sleep(1)
        raise AssertionError('custom_not_indexed')

    def capture_and_reconnect():
        args, item = configure(project, 1)
        before = wait_indexed(item)
        status = request('GET', f"/projects/{item['project_id']}/status")['provider_capture']
        prior = next(s for s in status if s['source_id'] == item['source_id'])
        result = cli(root, *args, timeout=360)
        assert result.returncode == 0
        binding = tomllib.loads((project / '.logchat/local.toml').read_text())
        assert binding['source_id'] == item['source_id']
        result = cli(root, 'local', 'providers', '--project', str(project),
                     '--state-dir', str(root / 'state'), '--poll', timeout=360)
        assert result.returncode == 0
        status = request('GET', f"/projects/{item['project_id']}/status")['provider_capture']
        current = next(s for s in status if s['source_id'] == item['source_id'])
        assert datetime.fromisoformat(current['cursor']) > stamp
        assert datetime.fromisoformat(current['cursor']) >= datetime.fromisoformat(prior['cursor'])
        assert current['observed_events'] == 1
        assert [m['chunk_id'] for m in memories(item)] == [m['chunk_id'] for m in before]
        (evidence / 'custom-reconnect.json').write_text(json.dumps({'before': prior, 'after': current}, indent=2))

    check('C14/C18 installed finite executable indexed; reconnect retains identity, cursor and history', capture_and_reconnect)

    def new_sources():
        for number in [2, 3]:
            _, item = configure(project, number, new=True)
            wait_indexed(item)
        assert len({b['source_id'] for b in bindings}) == 3
        assert len({b['project_id'] for b in bindings}) == 1
        for item in bindings:
            wait_indexed(item)
        _, item = configure(other, 4)
        wait_indexed(item)
        assert item['project_id'] != bindings[0]['project_id']

    check('C18/C20 two new-source identities coexist with original; separate project indexes independently', new_sources)

    def scoped_queries():
        assert len(bindings) == 4
        for item in bindings:
            result = request('POST', f"/projects/{item['project_id']}/ask", {
                'question': item['marker'], 'environment_ids': [item['environment_id']],
                'service': item['service'], 'timezone': 'UTC', 'start': since,
                'end': datetime.now(timezone.utc).isoformat()})
            (evidence / f"custom-context-{item['number']}.json").write_text(json.dumps(result, indent=2))
            selected = result.get('evidence', [])
            assert selected
            assert all(m['project_id'] == item['project_id'] and m['source_id'] == item['source_id']
                       and m['environment_id'] == item['environment_id'] for m in selected)
            text = json.dumps(selected)
            assert item['marker'] in text and f"custom-{item['number']}@example.test" in text
            assert all(b['marker'] not in text for b in bindings if b != item)
            assert 'LIB_AUTH_' not in text and 'PAY_DECLINED_' not in text and 'GlyphIndexMismatch' not in text
            assert sum(m['event_count'] for m in selected) == 1
            assert sum(m['duration_sum_ms'] for m in selected) == item['number'] * 101

    check('C20/C21/C22 four positive service-scoped queries preserve fields and exclude other sources/projects', scoped_queries)
    report = {'bindings': bindings, 'checks': cases,
              'passed': sum(c['status'] == 'passed' for c in cases),
              'failed': sum(c['status'] == 'failed' for c in cases)}
    (evidence / 'custom-walkthrough.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return report['failed'] == 0


@contextmanager
def inventory_container(name, rows):
    """Create only a fresh restricted owned container; remove it on every exit."""
    if not name.startswith('logchat-qa-'):
        raise ValueError('qa_container_name_required')
    code = 'import json,sys\nrows=json.loads(sys.argv[1])\n' + (
        'for i,row in enumerate(rows):\n'
        ' print(json.dumps(row),file=sys.stdout if i == 0 else sys.stderr,flush=True)\n')
    def docker(*args):
        return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=60)
    # No pull, mounts, networking, privileges or added capabilities.
    created = docker('create', '--name', name, '--pull', 'never', '--network', 'none',
                     '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                     'python:3.12.13-slim', 'python', '-u', '-c', code, json.dumps(rows))
    if created.returncode:
        raise RuntimeError('qa_container_create_failed')
    container_id = created.stdout.strip()
    try:
        result = docker('start', '--attach', container_id)
        assert result.returncode == 0
        assert rows[0]['event_id'] in result.stdout and rows[1]['event_id'] in result.stderr
        inspected = docker('inspect', container_id)
        assert inspected.returncode == 0
        config = json.loads(inspected.stdout)[0]
        host = config['HostConfig']
        assert host['NetworkMode'] == 'none' and host['ReadonlyRootfs']
        assert not config['Mounts'] and not host['Privileged'] and not host['CapAdd']
        assert 'ALL' in host['CapDrop'] and 'no-new-privileges' in host['SecurityOpt']
        yield name
    finally:
        removed = docker('rm', '--force', container_id)
        if removed.returncode or docker('inspect', container_id).returncode == 0:
            raise RuntimeError('qa_container_cleanup_failed')


def docker_walkthrough(root, client):
    """Actual Docker stdout/stderr and mixed file sources in one installed core."""
    import tomllib
    from uuid import uuid4
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    run_id = uuid4().hex
    project = root / 'projects/inventory app' / run_id
    project.mkdir()
    since = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    # Deliberately stale application time: indexed time must come from Docker logs.
    rows = [dict(timestamp='2000-01-01T00:00:00+00:00',
                 event_id=f'INVENTORY_{n}_{run_id}', service='inventory', level='error',
                 message=f'Inventory allocation refused INVENTORY_{n}_{run_id}',
                 error_code='STOCK_CONFLICT_73', email=f'inventory-{n}@example.test',
                 duration_ms=n * 111) for n in [1, 2]]
    file_row = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                    event_id=f'FILE_{run_id}', service='mixed-file', level='error',
                    message=f'File shipment refused FILE_{run_id}', error_code='SHIPMENT_LOCKED_84',
                    email='shipment@example.test', duration_ms=444)
    cases, bindings = [], []
    cleanup = {'container_created': False, 'container_removed': False}

    def request(method, path, body=None):
        response = client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()

    def check(label, action):
        try:
            action()
            cases.append({'check': label, 'status': 'passed'})
        except Exception as error:
            cases.append({'check': label, 'status': 'failed', 'error_category': type(error).__name__})

    def binding():
        value = tomllib.loads((project / '.logchat/local.toml').read_text())
        item = {k: value[k] for k in ['project_id', 'source_id', 'environment_id', 'kind']}
        bindings.append(item)
        return item

    def indexed(item, expected):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = request('GET', f"/projects/{item['project_id']}/search?environment_id={item['environment_id']}")
            memories = [m for m in result['evidence'] if m['identity']['source_id'] == item['source_id']]
            if memories:
                assert not result['limit_reached']
                assert sum(m['metrics']['event_count'] for m in memories) == len(expected)
                assert all(r['event_id'] in json.dumps(memories) for r in expected)
                assert all(m['identity']['project_id'] == item['project_id']
                           and m['identity']['environment_id'] == item['environment_id'] for m in memories)
                if item['kind'] == 'docker':
                    records = [r for m in memories for r in json.loads(m['compact_evidence'])['records']]
                    assert len(records) == 2
                    assert all(datetime.fromisoformat(since) <= datetime.fromisoformat(r['timestamp'])
                               <= datetime.now(timezone.utc) for r in records)
                (evidence / f"docker-indexed-{item['kind']}.json").write_text(json.dumps(memories, indent=2))
                return
            time.sleep(1)
        raise AssertionError('docker_mixed_not_indexed')

    def capture():
        try:
            with inventory_container('logchat-qa-inventory-' + run_id, rows) as name:
                cleanup['container_created'] = True
                result = cli(root, 'local', 'connect-provider', 'docker', '--project', str(project),
                             '--state-dir', str(root / 'state'), '--logchat-port', '18940',
                             '--container', name, '--since', since, timeout=360)
                assert result.returncode == 0
                item = binding()
                assert item['kind'] == 'docker'
                indexed(item, rows)
                status = request('GET', f"/projects/{item['project_id']}/status")['provider_capture']
                provider = next(s for s in status if s['source_id'] == item['source_id'])
                assert provider['observed_events'] == 2 and provider['kind'] == 'docker'
                assert datetime.fromisoformat(provider['cursor']) > datetime.fromisoformat(since)
                (evidence / 'docker-provider.json').write_text(json.dumps(provider, indent=2))
                # Stop polling before removing the finite producer; keep its memories.
                result = cli(root, 'local', 'disconnect', '--project', str(project))
                assert result.returncode == 0
            cleanup['container_removed'] = True
        finally:
            (evidence / 'docker-cleanup.json').write_text(json.dumps(cleanup, indent=2))

    check('C13 installed Docker timestamps/stdout/stderr indexed; restricted container removed', capture)

    def mixed():
        assert len(bindings) == 1
        log = project / 'shipment.ndjson'
        log.write_text(json.dumps(file_row) + '\n')
        result = cli(root, 'local', 'connect', '--project', str(project),
                     '--state-dir', str(root / 'state'), '--logchat-port', '18940',
                     '--log-file', str(log), '--from-start')
        assert result.returncode == 0
        item = binding()
        assert item['kind'] == 'push' and item['source_id'] != bindings[0]['source_id']
        assert item['project_id'] == bindings[0]['project_id']
        indexed(item, [file_row])
        indexed(bindings[0], rows)

    check('C20 Docker and file source memories coexist in one project/shared core', mixed)

    def scoped():
        assert len(bindings) == 2
        for item, expected, excluded in [(bindings[0], rows, [file_row]), (bindings[1], [file_row], rows)]:
            result = request('POST', f"/projects/{item['project_id']}/ask", {
                'question': expected[0]['error_code'], 'environment_ids': [item['environment_id']],
                'service': expected[0]['service'], 'timezone': 'UTC', 'start': since,
                'end': datetime.now(timezone.utc).isoformat()})
            (evidence / f"docker-context-{item['kind']}.json").write_text(json.dumps(result, indent=2))
            selected = result.get('evidence', [])
            assert selected
            assert all(m['project_id'] == item['project_id'] and m['source_id'] == item['source_id']
                       and m['environment_id'] == item['environment_id'] for m in selected)
            text = json.dumps(selected)
            assert all(r['event_id'] in text and r['email'] in text and r['error_code'] in text for r in expected)
            assert all(r['event_id'] not in text for r in excluded)
            assert all(marker not in text for marker in ['LIB_AUTH_', 'PAY_DECLINED_', 'GlyphIndexMismatch', 'CUSTOM_'])
            assert sum(m['event_count'] for m in selected) == len(expected)
            assert sum(m['duration_sum_ms'] for m in selected) == sum(r['duration_ms'] for r in expected)

    check('C21/C22 mixed-type selected fields/counts/durations exclude other source/projects', scoped)
    report = {'bindings': bindings, 'cleanup': cleanup, 'checks': cases,
              'passed': sum(c['status'] == 'passed' for c in cases),
              'failed': sum(c['status'] == 'failed' for c in cases)}
    (evidence / 'docker-walkthrough.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return report['failed'] == 0



@contextmanager
def file_producer(path):
    """Independent app process; acknowledged writes and bounded cleanup on every exit."""
    ack = path.with_suffix('.ack')
    program = """import json, pathlib, sys
path, ack = map(pathlib.Path, sys.argv[1:])
for number, line in enumerate(sys.stdin, 1):
    with path.open('a') as output:
        output.write(json.loads(line))
        output.flush()
    ack.write_text(str(number))
"""
    process = subprocess.Popen([sys.executable, '-u', '-c', program, str(path), str(ack)],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, text=True)
    sequence = 0
    def append(text):
        nonlocal sequence
        assert process.poll() is None
        sequence += 1
        process.stdin.write(json.dumps(text) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if ack.exists() and ack.read_text() == str(sequence):
                return
            if process.poll() is not None:
                break
            time.sleep(.02)
        raise RuntimeError('qa_producer_write_failed')
    try:
        yield process, append
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def recovery_walkthrough(root, client):
    """Installed partial-line, selected disconnect and basic restart/catch-up gates."""
    import tomllib
    from uuid import uuid4
    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    project = root / 'projects/auth app' / ('recovery-' + uuid4().hex)
    project.mkdir()
    state = root / 'state'
    cases, bindings, snapshots = [], [], {}
    event_times = {}
    def request(method, path):
        response = client.request(method, path)
        response.raise_for_status()
        return response.json()
    def require(*args):
        assert cli(root, *args).returncode == 0
    def connect(path, env):
        require('local', 'connect', '--project', str(project), '--state-dir', str(state),
                '--logchat-port', '18940', '--environment', env, '--log-file', str(path), '--from-start')
        value = tomllib.loads((project / '.logchat/local.toml').read_text())
        item = {k: value[k] for k in ['project_id', 'source_id', 'environment_id']}
        bindings.append(item)
        return item
    def status(item):
        sources = request('GET', f"/projects/{item['project_id']}/status")['file_capture']['sources']
        return next(s for s in sources if s['source_id'] == item['source_id'])
    def memories(item):
        result = request('GET', f"/projects/{item['project_id']}/search?environment_id={item['environment_id']}")
        assert not result['limit_reached']
        return [m for m in result['evidence'] if m['identity']['source_id'] == item['source_id']]
    def indexed(item, expected):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            values = memories(item)
            count = sum(m['metrics']['event_count'] for m in values)
            assert count <= len(expected)
            if count == len(expected):
                records = [record for memory in values
                           for record in json.loads(memory['compact_evidence'])['records']]
                assert len(records) == len(expected)
                assert sorted(record['timestamp'] for record in records) == sorted(event_times[key] for key in expected)
                assert all(record['service'] == 'recovery' for record in records)
                assert sum(m['metrics']['duration_sum_ms'] for m in values) == 123 * len(expected)
                assert all(m['identity']['project_id'] == item['project_id']
                           and m['identity']['environment_id'] == item['environment_id'] for m in values)
                return values
            time.sleep(1)
        raise AssertionError('recovery_not_indexed')
    def check(label, action):
        try:
            action()
            cases.append({'check': label, 'status': 'passed'})
        except Exception as error:
            cases.append({'check': label, 'status': 'failed', 'error_category': type(error).__name__})
    def row(marker):
        assert marker not in event_times
        event_times[marker] = datetime.now(timezone.utc).isoformat()
        return json.dumps(dict(timestamp=event_times[marker], level='error',
                               service='recovery', message='Recovery lookup refused ' + marker,
                               error_code='RECOVERY_LOCK_95', email='recovery@example.test', duration_ms=123))
    marker = project.name.replace('-', '_')
    expected = [marker + '_' + str(n) for n in range(4)]
    active, selected = project / 'active.ndjson', project / 'selected.ndjson'
    active.touch()
    selected.touch()
    cleanup = {'producer_stopped': False}
    producer = None
    try:
        with file_producer(active) as (producer, append):
            def partial():
                item = connect(active, 'dev')
                payload = row(expected[0])
                append(payload[:len(payload)//2])
                time.sleep(2)
                assert status(item)['offset'] == 0 and status(item)['accepted'] == 0
                assert not memories(item)
                append(payload[len(payload)//2:])
                time.sleep(2)
                assert status(item)['offset'] == 0 and status(item)['accepted'] == 0
                assert not memories(item)
                append('\n')
                snapshots['partial'] = indexed(item, expected[:1])
                assert status(item)['offset'] == active.stat().st_size
                assert status(item)['accepted'] == 1
            check('C08 partial fragments remain unaccepted until newline; exactly one indexed event', partial)
            def disconnect():
                assert cases[-1]['status'] == 'passed'
                selected.write_text(row(expected[1]) + '\n')
                item = connect(selected, 'preview')
                assert item['source_id'] != bindings[0]['source_id']
                assert item['project_id'] == bindings[0]['project_id']
                snapshots['selected_before'] = indexed(item, expected[1:2])
                require('local', 'disconnect', '--project', str(project))
                before = status(item)
                assert before['enabled'] == 0 and before['state'] == 'stopped'
                with selected.open('a') as output:
                    output.write(row('DISCONNECTED_' + marker) + '\n')
                append(row(expected[2]) + '\n')
                snapshots['active_after_disconnect'] = indexed(bindings[0], [expected[0], expected[2]])
                assert producer.poll() is None
                assert status(item)['offset'] == before['offset'] and status(item)['accepted'] == 1
                assert memories(item) == snapshots['selected_before']
                assert status(bindings[0])['enabled'] == 1
            check('C31 installed selected-source disconnect preserves history while another app continues', disconnect)
            def restart():
                assert cases[-1]['status'] == 'passed'
                before = memories(bindings[0])
                ids = {m['chunk_id'] for m in before}
                require('local', 'stop', '--state-dir', str(state))
                from logchat.local.lifecycle import managed_process
                assert not managed_process(state)[0] and producer.poll() is None
                append(row(expected[3]) + '\n')
                assert producer.poll() is None
                require('start', '--state-dir', str(state), '--port', '18940')
                values = indexed(bindings[0], [expected[0], expected[2], expected[3]])
                assert ids <= {m['chunk_id'] for m in values}
                assert status(bindings[0])['accepted'] == 3
                assert status(bindings[0])['offset'] == active.stat().st_size
                assert status(bindings[1])['enabled'] == 0
                assert memories(bindings[1]) == snapshots['selected_before']
                time.sleep(3)
                again = indexed(bindings[0], [expected[0], expected[2], expected[3]])
                assert {m['chunk_id'] for m in again} == {m['chunk_id'] for m in values}
                assert status(bindings[0])['accepted'] == 3
                snapshots['restart'] = again
                snapshots['final_status'] = [status(item) for item in bindings]
            check('C10/C32 installed restart catches independent producer output without duplicate totals', restart)
        cleanup['producer_stopped'] = producer.poll() is not None
        cleanup['producer_pid'] = producer.pid
    finally:
        if producer is not None:
            cleanup.update(producer_stopped=producer.poll() is not None, producer_pid=producer.pid)
        (evidence / 'recovery-cleanup.json').write_text(json.dumps(cleanup, indent=2))
    report = {'checks': cases, 'bindings': bindings, 'cleanup': cleanup,
              'passed': sum(c['status'] == 'passed' for c in cases),
              'failed': sum(c['status'] == 'failed' for c in cases)}
    (evidence / 'recovery-snapshots.json').write_text(json.dumps(snapshots, indent=2))
    (evidence / 'recovery-walkthrough.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    return report['failed'] == 0 and cleanup['producer_stopped']


def serve(root, *, walkthrough=False, payments=False, custom=False, docker=False, recovery=False):
    """Prepare a foreground review fixture with real models; always stop only owned service."""
    import httpx
    from logchat.local.lifecycle import control_token, managed_process, stop
    state = root / 'state'
    state.mkdir(exist_ok=True, mode=0o700)
    if managed_process(state)[0]:
        raise RuntimeError('qa_instance_already_running')
    def require(*args):
        result = cli(root, *args)
        if result.returncode:
            raise RuntimeError('qa_cli_operation_failed')
        return result
    owned = False
    previous = {}
    for key, value in environment(root).items():
        if key.startswith('LOGCHAT_'):
            previous[key] = os.environ.get(key)
            os.environ[key] = value
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    old_handlers = {s: signal.signal(s, interrupted) for s in [signal.SIGINT, signal.SIGTERM]}
    try:
        require('install', '--yes', '--model', 'mistral:7b', '--state-dir', str(state), '--port', '18940')
        require('start', '--state-dir', str(state), '--port', '18940')
        owned = True
        with httpx.Client(base_url='http://127.0.0.1:18940', trust_env=False, timeout=180,
                          headers={'Authorization': 'Bearer ' + control_token(state)}) as client:
            def request(method, path, body=None):
                response = client.request(method, path, json=body)
                response.raise_for_status()
                return response.json()
            projects = request('GET', '/projects')
            project = next((p for p in projects if p['path'] == str(root / 'projects/auth app')), None)
            if project is None:
                project = request('POST', '/projects', {'name': 'Synthetic auth app', 'path': str(root / 'projects/auth app')})
            environments = request('GET', f"/projects/{project['id']}/environments")
            sources = request('GET', f"/projects/{project['id']}/sources")
            # Reconcile partial preparation after interruption without duplicating accepted sources.
            for env in ['dev', 'preview', 'prod']:
                if not any(e['name'] == env for e in environments):
                    request('POST', f"/projects/{project['id']}/environments", {'name': env})
                source = next((s for s in sources if s['name'] == 'auth ' + env), None)
                if source is None:
                    source = request('POST', f"/projects/{project['id']}/sources",
                                     {'name': 'auth ' + env, 'environment': env, 'kind': 'push'})
                request('POST', f"/projects/{project['id']}/sources/{source['id']}/file",
                        {'path': str(root / f'projects/auth app/{env}.ndjson'), 'from_start': True})
            for name in ['payments app', 'console app', 'inventory app', 'custom cli app']:
                if not any(p['path'] == str(root / 'projects' / name) for p in projects):
                    request('POST', '/projects', {'name': 'Synthetic ' + name, 'path': str(root / 'projects' / name)})
            if payments:
                return payments_walkthrough(root, client)
            if custom:
                return custom_walkthrough(root, client)
            if docker:
                return docker_walkthrough(root, client)
            if recovery:
                return recovery_walkthrough(root, client)
            if walkthrough:
                from qa_browser import walkthrough as run_walkthrough
                return run_walkthrough(root, client)
            print('Review fixture http://127.0.0.1:18940; synthetic auth file sources ingest asynchronously. '
                  'Other app projects are empty until the multi-source walkthrough. Ctrl-C cleans owned service.', flush=True)
            while True:
                time.sleep(1)
    finally:
        if owned:
            stop(state)
            evidence = root / 'evidence'
            evidence.mkdir(exist_ok=True)
            (evidence / 'cleanup.json').write_text(json.dumps({
                'owned_service_running': bool(managed_process(state)[0]),
                'host_capture_enabled': False,
                'demo_servers_started': 0,
                'containers_started': int(docker),
                'mode': 'recovery-walkthrough' if recovery else ('docker-walkthrough' if docker else ('custom-walkthrough' if custom else ('payments-walkthrough' if payments else ('walkthrough' if walkthrough else 'review')))),
            }, indent=2))
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, help='Disposable run directory outside checkout')
    parser.add_argument('--prepare', action='store_true', help='Write synthetic projects without starting services')
    parser.add_argument('--checks', action='store_true', help='Run installed help and mocked adapter command contracts')
    parser.add_argument('--serve', action='store_true', help='Foreground real-model review fixture; Ctrl-C stops owned service')
    parser.add_argument('--walkthrough', action='store_true', help='Real Chrome console/reader and installed-model checks; stop owned service on completion')
    parser.add_argument('--payments-walkthrough', action='store_true', help='Installed wrapped payments ingestion, indexing and scoped query; always stop owned service')
    parser.add_argument('--custom-walkthrough', action='store_true', help='Installed custom CLI capture, reconnect, two new sources and scoped queries; always stop owned service')
    parser.add_argument('--docker-walkthrough', action='store_true', help='Cached restricted Docker inventory plus file in one project; scoped real-model queries and cleanup')
    parser.add_argument('--recovery-walkthrough', action='store_true', help='Installed file partial-line, selected disconnect and independent producer restart; always clean up')
    args = parser.parse_args()
    root = run_root(args.run_dir)
    if not any([args.prepare, args.checks, args.serve, args.walkthrough, args.payments_walkthrough, args.custom_walkthrough, args.docker_walkthrough, args.recovery_walkthrough]):
        parser.error('select --prepare, --checks, --serve or a --*-walkthrough')
    if args.prepare or args.serve or args.walkthrough or args.payments_walkthrough or args.custom_walkthrough or args.docker_walkthrough or args.recovery_walkthrough:
        prepare(root)
    if args.checks and not checks(root):
        return 1
    if args.serve or args.walkthrough or args.payments_walkthrough or args.custom_walkthrough or args.docker_walkthrough or args.recovery_walkthrough:
        if serve(root, walkthrough=args.walkthrough, payments=args.payments_walkthrough, custom=args.custom_walkthrough, docker=args.docker_walkthrough, recovery=args.recovery_walkthrough) is False:
            return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as error:
        # Content-free failures: never render command/API/provider output or credentials.
        print('QA failed: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
