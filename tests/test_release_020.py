"""Synthetic release acceptance: no host collectors, credentials or real logs."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from logchat.local.app import create_app, _optional_model_answer
from logchat.local.context import advance, empty, MAX_CHARS, terms
import test_portable_backend as _fixtures


class ReleaseAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'state'
        self.app = create_app(self.directory, 18765, capture_host=False)
        self.store = self.app.state.store
        self.project = self.store.create_project('synthetic')
        self.environment = self.store.list_environments(self.project['id'])[0]['id']
        self.http = TestClient(self.app, base_url='http://127.0.0.1:18765', headers={'Authorization': 'Bearer ' + self.store.control_token})
        self.addCleanup(self.http.close)
        self.prefix = '/projects/' + self.project['id']
        self.conversation = self.http.post(self.prefix + '/conversations', json={}).json()['id']
        self.turn_path = self.prefix + '/conversations/' + self.conversation + '/messages'

    def turn(self, question, **kwargs):
        response = self.http.post(self.turn_path, json={'question': question, **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_environment_optin_is_explicit_authenticated_durable_and_reversible(self):
        self.store.create_environment(self.project['id'], 'prod')
        self.assertEqual(self.http.get('/settings/ui').json(), {'environments_enabled': False})
        self.assertEqual(self.http.put('/settings/ui', headers={'Authorization': ''}, json={'environments_enabled': True}).status_code, 401)
        source = self.store.create_source(self.project['id'], 'fixture', 'dev', 'push', None)
        self.assertEqual(self.http.put('/settings/ui', headers={'Authorization': 'Bearer ' + source['token']}, json={'environments_enabled': True}).status_code, 401)
        self.assertEqual(self.http.put('/settings/ui', json={'environments_enabled': True}).status_code, 200)
        restarted = create_app(self.directory, 18765, capture_host=False)
        self.assertEqual(restarted.state.store.ui_preferences(), {'environments_enabled': True})
        self.http.put('/settings/ui', json={'environments_enabled': False})
        self.assertFalse(restarted.state.store.ui_preferences()['environments_enabled'])

    def test_growing_chat_latest_page_restart_earlier_recall_and_constraints(self):
        first = self.turn('Only inspect api. Never infer causes. Investigate zephyr queue timeout.', environment_ids=[self.environment], timezone='UTC', service='api', start='2026-10-01T00:00:00Z', end='2026-10-02T00:00:00Z')
        for index in range(255):
            self.turn(f'Independent topic {index} for database latency')
        page = self.http.get(self.prefix + '/conversations/' + self.conversation).json()
        self.assertEqual(len(page['messages']), 500)
        self.assertEqual(page['page'], {'direction': 'latest', 'limit': 500, 'total_messages': 512, 'has_older': True})
        self.assertEqual(page['messages'][-2]['content'], 'Independent topic 254 for database latency')
        self.assertNotIn(first['user_message']['id'], [m['id'] for m in page['messages']])
        restart = create_app(self.directory, 18765, capture_host=False)
        with TestClient(restart, base_url='http://127.0.0.1:18765', headers={'Authorization': 'Bearer ' + restart.state.store.control_token}) as http:
            context_path = self.prefix + '/conversations/' + self.conversation + '/context'
            memory = http.get(context_path, params={'q': 'Return to earlier zephyr problem'}).json()
            self.assertEqual(memory['turn_count'], 256)
            self.assertTrue(any('zephyr' in text for text in memory['recalled']))
            self.assertTrue(any('Never infer causes' in text for text in memory['constraints']))
            self.assertEqual(memory['scope']['service'], 'api')
            self.assertLessEqual(memory['metadata']['checkpoint_chars'], MAX_CHARS)
            self.assertFalse(memory['metadata']['memory_is_evidence'])
            fresh = http.get(context_path, params={'q': 'Investigate unrelated payment failure'}).json()
            self.assertEqual(fresh['recalled'], [])
            self.assertNotIn(first['result']['answer'], fresh['recent'])
            self.assertEqual(http.get(context_path, headers={'Authorization': ''}).status_code, 401)
            foreign = self.store.create_project('foreign')
            self.assertEqual(http.get(context_path.replace(self.project['id'], foreign['id'])).status_code, 404)

    def test_retries_update_checkpoint_and_question_index_exactly_once(self):
        request_id = str(uuid4())
        body = dict(environment_ids=[self.environment], request_id=request_id)
        first = self.turn('What evidence supports api?', **body)
        replay = self.turn('What evidence supports api?', **body)
        self.assertEqual(first, replay)
        self.assertEqual(self.http.post(self.turn_path, json={'question': 'different', **body}).status_code, 409)
        page = self.http.get(self.prefix + '/conversations/' + self.conversation).json()
        self.assertEqual(page['context']['turn_count'], 1)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM conversation_questions WHERE conversation_id=?', (self.conversation,)).fetchone()[0], 1)

    def test_explicit_environment_service_window_win_over_previous_comparison(self):
        prod = self.store.create_environment(self.project['id'], 'prod')['id']
        self.turn('Compare api', environment_ids=[self.environment], service='api', timezone='UTC', start='2026-10-01T00:00:00Z', end='2026-10-02T00:00:00Z', compare_start='2026-09-01T00:00:00Z', compare_end='2026-09-02T00:00:00Z')
        output = self.turn('Investigate worker', environment_ids=[prod], service='worker', start='2026-08-01T00:00:00Z', end='2026-08-02T00:00:00Z')
        cell, = output['result']['plan']['cells']
        self.assertEqual(cell['environment'], 'prod')
        self.assertEqual(cell['window']['start'], '2026-08-01T00:00:00+00:00')
        context = self.http.get(self.prefix + '/conversations/' + self.conversation + '/context').json()
        self.assertEqual(context['scope']['service'], 'worker')
        self.assertIsNone(context['scope']['compare_start'])
        relative = self.turn('What changed over 48hours?')['result']
        from datetime import datetime
        window, = [cell['window'] for cell in relative['plan']['cells']]
        self.assertEqual((datetime.fromisoformat(window['end']) - datetime.fromisoformat(window['start'])).total_seconds(), 48*3600)

    def test_fresh_topic_retrieval_ignores_prior_terms_and_old_reference_recalls_evidence(self):
        from datetime import datetime, timezone
        from pipeline.types import LogEvent
        source = self.store.create_source(self.project['id'], 'fixture', 'dev', 'push', None)
        for service in ('zephyr', 'database'):
            self.store.ingest(self.project['id'], source['id'], [LogEvent(event_id=str(uuid4()), ts=datetime(2026, 10, 1, 12, tzinfo=timezone.utc), source='fixture', service=service, level='error', message='timeout', fingerprint=service)])
        self.turn('Investigate zephyr', environment_ids=[self.environment], timezone='UTC', start='2026-10-01T00:00:00Z', end='2026-10-02T00:00:00Z')
        for _ in range(5):
            fresh = self.turn('Investigate database')
            self.assertEqual({row['service'] for row in fresh['result']['evidence']}, {'database'})
            self.assertEqual(fresh['result']['context_metadata']['recalled_count'], 0)
        old = self.turn('Return to earlier zephyr')
        self.assertEqual({row['service'] for row in old['result']['evidence']}, {'zephyr'})
        self.assertGreater(old['result']['context_metadata']['recalled_count'], 0)
        self.assertEqual(set(old['result']['cited_evidence_ids']), {row['id'] for row in old['result']['evidence']})

    def test_legacy_conversations_backfill_and_unicode_checkpoint_bound(self):
        value = empty()
        for index in range(300):
            value = advance(value, ('Only inspect ' + '界'*1500 + str(index)), {'service': '界'*200})
            self.assertLessEqual(len(json.dumps(value)), MAX_CHARS)
        self.turn('Investigate oldtopic', environment_ids=[self.environment])
        with self.store.connection(write=True) as conn:
            conn.execute('DELETE FROM conversation_context WHERE conversation_id=?', (self.conversation,))
            conn.execute('DELETE FROM conversation_questions WHERE conversation_id=?', (self.conversation,))
        app = create_app(self.directory, 18765, capture_host=False)
        page = app.state.store.conversation(self.conversation, self.project['id'])
        self.assertEqual(page['context']['turn_count'], 1)
        with app.state.store.connection() as conn:
            from logchat.local.context import read
            self.assertIn('Investigate oldtopic', read(conn, self.conversation, 'Earlier oldtopic')['recalled'])

    def test_same_scope_database_cache_temporal_and_anaphoric_retrieval(self):
        from datetime import datetime, timezone
        from pipeline.types import LogEvent
        source = self.store.create_source(self.project['id'], 'fixture', 'dev', 'push', None)
        for topic, message in [('database', 'database connection timeout'), ('cache', 'cache redis unavailable')]:
            self.store.ingest(self.project['id'], source['id'], [LogEvent(
                event_id=str(uuid4()), ts=datetime(2026, 10, 1, 10, 5, tzinfo=timezone.utc),
                source='fixture', service='api', level='error', message=message,
                fingerprint=topic, duration_ms=100 if topic == 'database' else 200)])
        scope = dict(environment_ids=[self.environment], timezone='UTC', service='api',
                     start='2026-10-01T10:00:00Z', end='2026-10-01T10:30:00Z')

        def topic(output, expected, recalled=None):
            self.assertEqual({name for row in output['result']['evidence'] for name in ('database', 'cache') if name in row['summary']}, expected)
            self.assertEqual(len(output['result']['evidence']), len(expected))
            metric = output['result']['plan']['cells'][0]['metrics']
            mean = sum(100 if name == 'database' else 200 for name in expected) / len(expected) if expected else None
            self.assertEqual(metric['duration_mean_ms'], mean)
            self.assertEqual(metric['duration_count'], len(expected))
            if recalled is not None:
                self.assertEqual(output['result']['context_metadata']['recalled_count'], recalled)

        topic(self.turn('database timeouts on October 1', **scope), {'database'}, 0)
        exact = 'Were duration measurements available for those failures?'
        self.assertEqual(terms(exact), [])
        self.assertEqual(terms('Were duration measurements available for those GPU failures?'), ['gpu'])
        for _ in range(3):
            topic(self.turn(exact), {'database'}, 1)
        topic(self.turn('Explain those'), {'database'}, 1)
        topic(self.turn('Explain those again'), {'database'}, 1)
        topic(self.turn('What was their mean duration?'), {'database'}, 1)
        topic(self.turn('cache before October 2'), {'cache'}, 0)
        topic(self.turn('cache earlier day'), {'cache'}, 0)
        topic(self.turn('Which database failures were observed on October 1?'), {'database'}, 0)
        topic(self.turn('Which failures were observed on October 1?'), {'database', 'cache'}, 0)
        topic(self.turn('GPU failures on October 1'), set(), 0)
        # Shared calendar and question scaffolding cannot recall arbitrary history.
        topic(self.turn('Return to earlier GPU failures on October 1'), set(), 1)
        context_path = self.prefix + '/conversations/' + self.conversation + '/context'
        self.assertEqual(self.http.get(context_path, params={'q': 'Return to earlier October 1'}).json()['recalled'], [])
        self.assertEqual(self.http.get(context_path, params={'q': 'Earlier unmentioned failures on October 1'}).json()['recalled'], [])

        for index in range(255):
            self.turn(f'Independent topic {index} for cache')
        restarted = create_app(self.directory, 18765, capture_host=False)
        with TestClient(restarted, base_url='http://127.0.0.1:18765', headers={'Authorization': 'Bearer ' + restarted.state.store.control_token}) as http:
            old = http.post(self.turn_path, json={'question': 'Return to earlier database'}).json()
            topic(old, {'database'})
            self.assertGreater(old['result']['context_metadata']['recalled_count'], 0)
            topic(http.post(self.turn_path, json={'question': 'Explain those'}).json(), {'database'}, 1)
            topic(http.post(self.turn_path, json={'question': 'Explain those again'}).json(), {'database'}, 1)
            for _ in range(3):
                topic(http.post(self.turn_path, json={'question': exact}).json(), {'database'}, 1)
            # Real backend optional inference receives recalled user context and only database evidence.
            class Models:
                async def generate(model, instruction, context, schema):
                    self.assertEqual(context['question'], exact)
                    self.assertEqual(context['conversational_memory_not_evidence']['recalled'], ['Return to earlier database'])
                    self.assertEqual(len(context['evidence']), 1)
                    self.assertIn('database', context['evidence'][0]['summary'])
                    self.assertEqual(context['plan']['cells'][0]['metrics']['duration_mean_ms'], 100)
                    return {'answer': 'Unsupported cause discarded', 'cited_evidence_ids': [context['evidence'][0]['id']]}
            with patch.object(restarted.state.store, 'settings', return_value={'provider': 'openai_compatible', 'base_url': 'https://synthetic.example/v1', 'chat_model': 'fixture'}), patch('logchat.local.app.OpenAICompatibleModels', return_value=Models()):
                output = http.post(self.turn_path, json={'question': exact}).json()
            topic(output, {'database'}, 1)
            self.assertEqual(output['result']['provenance']['computed_findings_provider'], 'extractive')
            self.assertTrue(output['result']['model_output_policy'].startswith('evidence_selection_only'))
            self.assertNotIn('Unsupported cause', output['result']['answer'])
            direct_restart = create_app(self.directory, 18765, capture_host=False)
            with TestClient(direct_restart, base_url='http://127.0.0.1:18765', headers={'Authorization': 'Bearer ' + direct_restart.state.store.control_token}) as fresh_http:
                for _ in range(3):
                    topic(fresh_http.post(self.turn_path, json={'question': exact}).json(), {'database'}, 1)
            topic(http.post(self.turn_path, json={'question': 'Were duration measurements available for those GPU failures?'}).json(), set())
            topic(http.post(self.turn_path, json={'question': 'Were duration measurements available for those unmentioned payments failures?'}).json(), set(), 0)
            second_restart = create_app(self.directory, 18765, capture_host=False)
            with TestClient(second_restart, base_url='http://127.0.0.1:18765', headers={'Authorization': 'Bearer ' + second_restart.state.store.control_token}) as second_http:
                topic(second_http.post(self.turn_path, json={'question': 'cache before October 2'}).json(), {'cache'}, 0)

    def test_handoff_integration_requires_binding_and_reads_existing_directory_only(self):
        from typer.testing import CliRunner
        from cli.main import app as cli_app
        directory = Path(self.temp.name) / "bound folder O'Brien;$HOME"
        directory.mkdir()
        runner = CliRunner()
        with patch('logchat.local.cli.attach_project', side_effect=AssertionError('Must not attach')), patch('logchat.local.client.read_credential', side_effect=AssertionError('Must not read credentials')):
            missing = runner.invoke(cli_app, ['local', 'integration', '--project', str(directory)])
            self.assertNotEqual(missing.exit_code, 0)
            self.assertIn('Run logchat local attach', missing.output)
            self.assertEqual(list(directory.iterdir()), [])
            # Host-provided read-only binding directories need no source attachment.
            config = directory / '.logchat' / 'local.toml'
            config.parent.mkdir()
            original = 'project_id = "synthetic"\napi_url = "http://127.0.0.1:18765"\n'
            config.write_text(original)
            bound = runner.invoke(cli_app, ['local', 'integration', '--project', str(directory)])
            self.assertEqual(bound.exit_code, 0, bound.output)
            integration = json.loads(bound.output)['mcpServers']['logchat']
            self.assertEqual(integration['args'][-2:], ['--project', str(directory.resolve())])
            self.assertEqual(config.read_text(), original)
            self.assertEqual(list(config.parent.iterdir()), [config])

    def test_ui_runtime_inspector_copy_navigation_and_default_hidden_contract(self):
        result = subprocess.run(['node', 'tests/native_ui_contract.cjs'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SafeModelPolicy(unittest.IsolatedAsyncioTestCase):
    async def test_valid_citations_do_not_publish_unsupported_causes_or_unknown_labels(self):
        identifiers, result = _fixtures.PortableBackendTests.model_result()
        class Store:
            def settings(self): return {'provider': 'openai_compatible', 'base_url': 'https://synthetic.example/v1', 'chat_model': 'fixture'}
            def model_api_key(self): return None
        class Models:
            async def generate(self, *_):
                return {'answer': f'The cause is a broken deployment. Guaranteed memory leak [E999] [{identifiers[0]}].', 'cited_evidence_ids': [identifiers[0]]}
        with patch('logchat.local.app.OpenAICompatibleModels', return_value=Models()):
            output = await _optional_model_answer(Store(), result, 'Why?')
        self.assertNotIn('broken deployment', output['answer'])
        self.assertNotIn('Guaranteed', output['answer'])
        self.assertNotIn('E999', output['answer'])
        self.assertIn('Observed timeout aggregate', output['answer'])
        self.assertEqual(output['provenance']['provider'], 'openai_compatible')
        self.assertEqual(set(output['cited_evidence_ids']), set(identifiers))

    async def test_all_optional_model_input_is_bounded_with_large_checkpoint(self):
        identifiers, result = _fixtures.PortableBackendTests.model_result()
        class Store:
            def settings(self): return {'provider': 'openai_compatible', 'base_url': 'https://synthetic.example/v1', 'chat_model': 'fixture'}
            def model_api_key(self): return None
        class Models:
            async def generate(self, instruction, context, schema):
                self.context = context
                return {'answer': 'Observed', 'cited_evidence_ids': identifiers[:1]}
        model = Models()
        with patch('logchat.local.app.OpenAICompatibleModels', return_value=model):
            output = await _optional_model_answer(Store(), result, '界'*2000, conversational_context={'recent': ['x'*400]*12, 'constraints': ['x'*200]*16})
        # Over-budget input uses fallback, or inference gets <=24k chars.
        if hasattr(model, 'context'):
            self.assertLessEqual(len(json.dumps(model.context)), 24000)
        else:
            self.assertEqual(output['provenance']['status'], 'fallback')
