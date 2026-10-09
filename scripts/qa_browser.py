"""Actual Chrome interactions and real local-model checks for the owned QA fixture.

Called by qa_native --walkthrough. No fake API, profile copies or model downloads.
Only safe case categories and synthetic context/status are written to evidence.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import time
import uuid


def push_with_busy_retry(client, path, body, token, *, timeout=120):
    """Retry only explicit intake contention, keeping event IDs/content unchanged."""
    deadline = time.monotonic() + timeout
    retries = 0
    while True:
        response = client.post(path, json=body, headers={'Authorization': 'Bearer ' + token})
        if (response.status_code != 503 or
                response.headers.get('X-Logchat-Processing-Category') != 'intake_busy_retry_required' or
                time.monotonic() >= deadline):
            response.raise_for_status()
            return response.json(), retries
        retries += 1
        time.sleep(1)


def walkthrough(root, client):
    from connectors.browser_console import BrowserConsoleAdapter
    from playwright.sync_api import sync_playwright, expect

    evidence = root / 'evidence'
    evidence.mkdir(exist_ok=True)
    cases = []
    runtime_errors = []

    def check(identifier, label, action):
        try:
            action()
            cases.append(dict(id=identifier, check=label, status='passed'))
            return True
        except Exception as error:
            case = dict(id=identifier, check=label, status='failed', error_category=type(error).__name__)
            if getattr(error, 'response', None) is not None:
                case['http_status'] = error.response.status_code
                from logchat.local.raw_capture import SAFE_PROCESSING_FAILURES
                category = error.response.headers.get('X-Logchat-Processing-Category')
                if category in SAFE_PROCESSING_FAILURES:
                    case['processing_category'] = category
            cases.append(case)
            return False

    def request(method, path, body=None, token=None):
        headers = {'Authorization': 'Bearer ' + token} if token else None
        response = client.request(method, path, json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, indent=2))

    projects = request('GET', '/projects')
    auth = next(p for p in projects if p['path'] == str(root / 'projects/auth app'))
    # Retained review history can exceed the bounded relevance candidate budget.
    # Give this UI scenario a fresh logical project so both new intakes are the
    # complete expected support set, while preserving prior review memories.
    console_path = root / 'projects/console app' / ('reader-' + uuid.uuid4().hex)
    console_path.mkdir()
    console = request('POST', '/projects', dict(name='Synthetic console reader', path=str(console_path)))
    # Source credentials are protected outside evidence, not logged or returned.
    credential = root / f"credentials/qa-console-{console['id']}.json"
    credential.parent.mkdir(mode=0o700, exist_ok=True)
    if credential.exists():
        source = json.loads(credential.read_text())
    else:
        source = request('POST', f"/projects/{console['id']}/sources",
                         dict(name='explicit browser console', environment='dev', kind='push'))
        credential.write_text(json.dumps(source))
        credential.chmod(0o600)

    def status(project):
        return request('GET', f"/projects/{project['id']}/status")

    def query(project, environment, question, name):
        envs = request('GET', f"/projects/{project['id']}/environments")
        env = next(e for e in envs if e['name'] == environment)
        result = request('POST', f"/projects/{project['id']}/ask",
                         dict(question=question, environment_ids=[env['id']], timezone='UTC'))
        save(name, result)
        return result

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport=dict(width=1440, height=1000),
                                          permissions=['clipboard-read', 'clipboard-write'])
            page = context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: runtime_errors.append('pageerror'))
            # Report content-free browser failures, never console payloads.
            page.on('console', lambda message: runtime_errors.append('console_error') if message.type == 'error' else None)
            demo_page = context.new_page()
            records = []
            demo_page.on('console', lambda message: records.append(dict(
                message=message.text, level=message.type, timestamp=datetime.now(timezone.utc).isoformat())))

            def ingest_console():
                before = status(console)['memory']['application']['total_events']
                # Separate real intakes create separate immutable compact memories.
                # Both must be selected below; retained history cannot replace them.
                fresh_ids = []
                for index in range(2):
                    records.clear()
                    demo_page.goto((root / 'projects/console app/index.html').as_uri())
                    demo_page.get_by_role('button', name='Emit browser session error').click()
                    expect(demo_page.locator('#status')).to_have_text('Synthetic browser event emitted')
                    events = BrowserConsoleAdapter('console app').normalize_batch(records)
                    assert len(events) == 1
                    assert all(v in events[0].message for v in ['browser-user@example.test', '+15550001111', 'GlyphIndexMismatch'])
                    fresh_ids.append(json.loads(records[0]['message'])['event_id'])
                    values = []
                    for event in events:
                        value = asdict(event)
                        value['timestamp'] = value.pop('ts').isoformat()
                        values.append(value)
                    response, retries = push_with_busy_retry(client, f"/projects/{console['id']}/events",
                                       dict(source_id=source['id'], events=values), source['token'])
                    save(f'console-intake-{index}.json', dict(response=response, busy_retries=retries))
                assert len(set(fresh_ids)) == 2
                console_run.update(before=before, fresh_ids=fresh_ids)
            console_run = {}
            accepted = check('C12', 'Actual Chrome button -> explicit BrowserConsoleAdapter -> real semantic intake', ingest_console)
            demo_page.close()

            def wait_indexed():
                save('console-status.json', status(console))
                assert accepted, 'intake_not_accepted'
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    snapshot = status(console)
                    if snapshot['memory']['application']['total_events'] >= console_run['before'] + 2:
                        save('console-status.json', snapshot)
                        return
                    page.wait_for_timeout(1000)
                save('console-status.json', snapshot)
                raise AssertionError('console_not_indexed')
            check('C20', 'Second app indexed through shared semantic pipeline', wait_indexed)
            save('auth-status.json', status(auth))

            for environment in ['dev', 'preview', 'prod']:
                def scoped(env=environment):
                    result = query(auth, env, 'What context exists about refused session renewal?', f'auth-{env}-context.json')
                    selected = result.get('evidence', [])
                    assert selected, 'no_selected_evidence'
                    text = json.dumps(selected)
                    assert all(other.upper() + '-' not in text for other in ['dev', 'preview', 'prod'] if other != env)
                    env_id = next(e['id'] for e in request('GET', f"/projects/{auth['id']}/environments") if e['name'] == env)
                    sources = request('GET', f"/projects/{auth['id']}/sources")
                    source_id = next(s['id'] for s in sources if s['name'] == 'auth ' + env)
                    assert all(item['project_id'] == auth['id'] and item['environment_id'] == env_id
                               and item['source_id'] == source_id for item in selected)
                    rows = [json.loads(line) for line in (root / f'projects/auth app/{env}.ndjson').read_text().splitlines()]
                    browse = request('GET', f"/projects/{auth['id']}/search?environment_id={env_id}")
                    assert not browse['limit_reached']
                    indexed = [item for item in browse['evidence'] if item['identity']['source_id'] == source_id]
                    assert all(item['identity']['project_id'] == auth['id'] and item['identity']['environment_id'] == env_id for item in indexed)
                    indexed_count = sum(item['metrics']['event_count'] for item in indexed)
                    assert indexed_count == len(rows)
                    assert sum(item['event_count'] for item in selected) == len(rows)
                    assert sum(item['duration_sum_ms'] for item in selected) == sum(row['duration_ms'] for row in rows)
                    save(f'auth-{env}-measurements.json', {'input_events': len(rows),
                         'accepted_source_events': next(s['accepted'] for s in status(auth)['file_capture']['sources'] if s['source_id'] == source_id),
                         'indexed_source_events': indexed_count,
                         'selected_events': sum(item['event_count'] for item in selected),
                         'selected_duration_sum_ms': sum(item['duration_sum_ms'] for item in selected)})
                check('C21', f'Real auth {environment} query with colliding error terms and scoped evidence', scoped)

            def selected_console():
                result = query(console, 'dev', 'GlyphIndexMismatch', 'console-exact-context.json')
                text = json.dumps(result.get('evidence', []))
                assert all(v in text for v in ['browser-user@example.test', '+15550001111', 'GlyphIndexMismatch'])
                assert 'LIB_AUTH_' not in text
            check('C22', 'Real exact-code query retains selected structured identifiers and excludes auth project', selected_console)

            page.goto('http://127.0.0.1:18940')
            expect(page.locator('#app')).to_be_visible()

            def choose_console():
                page.locator('#project-picker').select_option(console['id'])
                expect(page.locator('#project-name')).to_have_text(console['name'])
                expect(page.locator('#question')).to_be_visible()
                assert page.locator('input[name=environment_ids]').count() == 0
            check('B02', 'Select real console project and read default hidden environment scope', choose_console)

            selected_memories = []

            def submit_query(question='What context exists about the browser refusing a session refresh?', filename='browser-console-context-response.json', minimum=1):
                page.locator('#question').fill(question)
                with page.expect_response(lambda r: '/messages' in r.url and r.request.method == 'POST', timeout=180000) as pending:
                    page.get_by_role('button', name='Ask', exact=True).click()
                response = pending.value
                assert response.ok
                result = response.json()
                save(filename, result)
                selected_memories[:] = result.get('result', result).get('evidence', [])
                assert len(selected_memories) >= minimum, 'insufficient_selected_evidence'
                if minimum >= 2:
                    selected_text = json.dumps(selected_memories)
                    assert all(identifier in selected_text for identifier in console_run['fresh_ids']), 'fresh_memories_not_selected'
                expect(page.locator('.answer-provenance')).to_be_visible()
                expect(page.get_by_role('button', name='Supporting memories', exact=True)).to_be_visible()
                page.screenshot(path=str(evidence / 'reader-desktop.png'), full_page=True)
            ready = check('B03/B04', 'Console paraphrase selects both fresh real compact memories', lambda: submit_query(minimum=2))

            def memories():
                try:
                    page.get_by_role('button', name='Supporting memories', exact=True).last.click()
                    expect(page.locator('#evidence-panel')).to_be_visible()
                    select = page.locator('#supporting-memory-select')
                    count = select.locator('option').count()
                    assert count == len(selected_memories)
                    expect(page.locator('.citation-map .truth-note').last).to_have_text(f'{count} supporting memories')
                    assert len({item['id'] for item in selected_memories}) == count
                    assert select.evaluate('(el) => el === document.activeElement')
                    for index in range(count):
                        select.select_option(str(index))
                        expect(page.locator('.memory-details .evidence-summary')).to_be_visible()
                        page.get_by_role('button', name='Copy memory', exact=True).click()
                        clipboard = page.evaluate('navigator.clipboard.readText()')
                        copied = json.loads(clipboard)
                        expected = selected_memories[index]
                        assert copied['memory']['id'] == expected['id']
                        assert copied['memory']['project_id'] == page.locator('#project-picker').input_value()
                        for key, value in copied['memory'].items():
                            assert value == expected[key], 'copied_memory_content_mismatch'
                        assert expected['summary'] == page.locator('.evidence-summary').inner_text()
                    page.keyboard.press('Escape')
                    expect(page.locator('#evidence-panel')).to_be_hidden()
                    assert page.get_by_role('button', name='Supporting memories', exact=True).last.evaluate('(el) => el === document.activeElement')
                finally:
                    if page.locator('#evidence-panel').is_visible():
                        page.get_by_role('button', name='Close evidence').click()
            if ready:
                check('B05/B06/B11', 'Supporting list, select every memory, copy, focus and Escape close', memories)

            page.get_by_role('button', name='Settings', exact=True).click()
            expect(page.get_by_label('Capture policy')).to_be_visible()
            before_model = request('GET', '/settings/models')
            before_capture = request('GET', '/settings/capture')
            before_ui = request('GET', '/settings/ui')
            # Restore through API in finally even if a control check fails.
            try:
                def installed_models():
                    page.get_by_role('button', name='Load installed models').click()
                    expect(page.get_by_label('Installed generation models').locator('option', has_text='mistral:7b')).to_have_count(1)
                check('B07', 'Actual Settings loads installed generation models', installed_models)

                def rejected_model():
                    page.get_by_label('Shared generation model').fill('qa-nonexistent-model')
                    with page.expect_response(lambda r: '/settings/shared-model' in r.url, timeout=180000) as pending:
                        page.get_by_role('button', name='Use shared model').click()
                    assert pending.value.status >= 400
                    expect(page.locator('.error-banner').first).to_be_visible()
                    assert request('GET', '/settings/models') == before_model
                check('B08', 'Missing model visible rejection preserves saved profile without fallback', rejected_model)

                def capture_policy():
                    for mode in ['retain_until_summarized', before_capture['mode']]:
                        page.get_by_label('Capture policy').select_option(mode)
                        with page.expect_response(lambda r: '/settings/capture' in r.url and r.request.method == 'PUT'):
                            page.get_by_role('button', name='Save retention settings').click()
                        assert request('GET', '/settings/capture')['mode'] == mode
                check('B09', 'Actual retention policy toggle and restore', capture_policy)

                def visibility():
                    page.get_by_label('Show environment controls').check()
                    with page.expect_response(lambda r: '/settings/ui' in r.url and r.request.method == 'PUT'):
                        page.get_by_role('button', name='Save visibility').click()
                    assert request('GET', '/settings/ui')['environments_enabled']
                    page.get_by_role('button', name='Investigations', exact=True).click()
                    expect(page.locator('input[name=environment_ids]').first).to_be_visible()
                    page.locator('#project-picker').select_option(auth['id'])
                    page.get_by_role('button', name='New investigation', exact=True).click()
                    envs = request('GET', f"/projects/{auth['id']}/environments")
                    preview = next(e for e in envs if e['name'] == 'preview')
                    for checkbox in page.locator('input[name=environment_ids]').all():
                        checkbox.set_checked(checkbox.input_value() == preview['id'])
                    check('B03/B04', 'Actual preview-only auth paraphrase shows selected evidence/provenance',
                          lambda: submit_query('What context exists about refused session renewal?', 'browser-auth-context-response.json'))
                    check('B05/B06/B11', 'Real auth supporting list, every selection, clipboard, focus and Escape', memories)
                    page.get_by_role('button', name='Settings', exact=True).click()
                    page.get_by_label('Show environment controls').uncheck()
                    with page.expect_response(lambda r: '/settings/ui' in r.url and r.request.method == 'PUT'):
                        page.get_by_role('button', name='Save visibility').click()
                    page.get_by_role('button', name='Investigations', exact=True).click()
                    assert page.locator('input[name=environment_ids]').count() == 0
                check('B10', 'Opt into environment controls and hide them again through Settings', visibility)

                def layout():
                    def bounded(selector, name):
                        measurements = page.locator(selector).evaluate('''el => ({
                            client: el.clientWidth, scroll: el.scrollWidth,
                            controls: [...el.querySelectorAll('input, select, textarea, button')].map(control => {
                                const r = control.getBoundingClientRect();
                                return {tag: control.tagName, id: control.id, left: r.left, right: r.right, width: r.width};
                            })
                        })''')
                        save(f'layout-{name}-{width}.json', measurements)
                        page.screenshot(path=str(evidence / f'{name}-{width}.png'), full_page=True)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert measurements['scroll'] <= measurements['client'], name + '_internal_overflow'
                        assert all(not c['width'] or 0 <= c['left'] <= c['right'] <= width
                                   for c in measurements['controls']), name + '_control_clipping'

                    for width in [1440, 390]:
                        page.set_viewport_size(dict(width=width, height=1000))
                        if width == 390:
                            page.get_by_role('button', name='Open navigation').click()
                        page.get_by_role('button', name='Settings', exact=True).click()
                        expect(page.get_by_label('Capture policy')).to_be_visible()
                        expect(page.locator('#sidebar')).not_to_have_class('sidebar is-open')
                        page.wait_for_timeout(250)  # wait for the real navigation close transition
                        bounded('#workspace', 'settings')
                        if width == 390:
                            page.get_by_role('button', name='Open navigation').click()
                        page.get_by_role('button', name='Investigations', exact=True).click()
                        expect(page.locator('#question')).to_be_visible()
                        page.wait_for_timeout(250)
                        expect(page.locator('.answer-provenance').last).to_be_visible()
                        bounded('#workspace', 'reader')
                        if width == 390:
                            page.get_by_role('button', name='Open navigation').click()
                            page.locator('.conversation-item.active').click()
                            expect(page.locator('#sidebar')).not_to_have_class('sidebar is-open')
                            page.wait_for_timeout(250)
                            bounded('#workspace', 'reader-reopened')
                        page.get_by_role('button', name='Supporting memories', exact=True).last.click()
                        expect(page.locator('.memory-details .evidence-summary')).to_be_visible()
                        bounded('#evidence-panel', 'memory-inspector')
                        page.keyboard.press('Escape')
                        expect(page.locator('#evidence-panel')).to_be_hidden()
                        memories()
                check('B12', 'Desktop and 390px populated reader/settings/inspector and mobile navigation fit', layout)

                def empty_reader():
                    # A genuine project with no sources/memories, using the real API.
                    empty_path = str(root / 'projects/empty reader app')
                    existing = request('GET', '/projects')
                    empty = next((p for p in existing if p['path'] == empty_path), None)
                    if empty is None:
                        empty = request('POST', '/projects', dict(name='Synthetic empty reader app', path=empty_path))
                    assert status(empty)['memory']['application']['total_events'] == 0
                    page.set_viewport_size(dict(width=1440, height=1000))
                    page.goto('http://127.0.0.1:18940')
                    page.locator('#project-picker').select_option(empty['id'])
                    expect(page.get_by_role('heading', name='Ask what the evidence supports.')).to_be_visible()
                    expect(page.locator('#question')).to_be_empty()
                    page.get_by_role('button', name='Ask', exact=True).click()
                    assert page.locator('.message').count() == 0
                    page.locator('#question').fill('What evidence exists about session renewal errors?')
                    with page.expect_response(lambda r: '/messages' in r.url and r.request.method == 'POST', timeout=180000) as pending:
                        page.get_by_role('button', name='Ask', exact=True).click()
                    assert pending.value.ok
                    result = pending.value.json()['result']
                    save('browser-empty-context-response.json', result)
                    assert result['evidence'] == []
                    expect(page.locator('.answer-copy')).to_have_text(result['answer'])
                    expect(page.locator('.citation-map .truth-note')).to_have_text('0 supporting memories')
                    page.get_by_role('button', name='Supporting memories', exact=True).click()
                    expect(page.get_by_text('No supporting memories were retrieved for this answer.', exact=True)).to_be_visible()
                    assert page.locator('#supporting-memory-select').count() == 0
                    assert page.get_by_role('button', name='Close evidence').evaluate('(el) => el === document.activeElement')
                    page.keyboard.press('Escape')
                    expect(page.locator('#evidence-panel')).to_be_hidden()
                    page.screenshot(path=str(evidence / 'empty-reader.png'), full_page=True)
                check('B03/B04/B05', 'Actual empty-project reader, blank submission, no-evidence answer and empty support panel', empty_reader)

                def unavailable_reader():
                    try:
                        context.set_offline(True)
                        page.locator('#question').fill('What context exists about unavailable capture?')
                        page.get_by_role('button', name='Ask', exact=True).click()
                        expect(page.locator('.answer-copy').last).to_contain_text('The question could not be completed:')
                        expect(page.locator('.gaps').last).to_contain_text('No new evidence was added')
                        expect(page.locator('.citation-map .truth-note').last).to_have_text('0 supporting memories')
                        page.screenshot(path=str(evidence / 'unavailable-reader.png'), full_page=True)
                    finally:
                        context.set_offline(False)
                check('B02', 'Actual browser transport outage exposes safe reader failure without evidence', unavailable_reader)

                def unavailable_models():
                    page.get_by_role('button', name='Settings', exact=True).click()
                    try:
                        context.set_offline(True)
                        page.get_by_role('button', name='Load installed models').click()
                        expect(page.locator('.error-banner').last).to_be_visible()
                        expect(page.get_by_role('button', name='Load installed models')).to_be_enabled()
                        page.screenshot(path=str(evidence / 'unavailable-models.png'), full_page=True)
                    finally:
                        context.set_offline(False)
                    assert request('GET', '/settings/models') == before_model
                check('B07', 'Actual browser transport outage exposes Settings model-list error and preserves profile', unavailable_models)
            finally:
                request('PUT', '/settings/capture', {k: before_capture[k] for k in ['mode', 'max_bytes', 'retention_seconds']})
                request('PUT', '/settings/ui', dict(environments_enabled=before_ui['environments_enabled']))
            # The intentional missing-model HTTP error can emit a browser network
            # console error. Unhandled JS errors are never expected.
            check('B13', 'No unhandled JavaScript errors; expected negative-model network errors labelled',
                  lambda: expect_no_page_errors(runtime_errors))
            save('browser-runtime.json', dict(categories=runtime_errors,
                 expected_network_failure='Missing-model rejection B08 and explicit offline reader/model-list variants; browser may log network errors'))
            context.close()
        finally:
            browser.close()
            report = dict(mode='Actual headless installed Chrome + real installed Logchat/Ollama; no fake API or live cloud',
                          checks=cases, passed=sum(c['status'] == 'passed' for c in cases),
                          failed=sum(c['status'] == 'failed' for c in cases))
            save('walkthrough.json', report)
            print(json.dumps({k: report[k] for k in ['passed', 'failed']}), flush=True)
    return report['failed'] == 0


def expect_no_page_errors(categories):
    assert 'pageerror' not in categories
