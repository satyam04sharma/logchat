"""Synthetic supporting-memory inspection across native API, client and real MCP."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from mcp import Client

from logchat.client import LogchatError
from logchat.local.app import create_app
from logchat.local.client import LocalLogchatClient
from logchat.mcp import create_server
from pipeline.types import LogEvent


class MemoryInspectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = create_app(self.root / "state", 18765, capture_host=False)
        store = self.app.state.store
        self.project = store.create_project("bound")
        self.other = store.create_project("other")
        self.ids = []
        for project in (self.project, self.other):
            source = store.create_source(project["id"], "fixture", "dev", "push", None)
            store.ingest(project["id"], source["id"], [LogEvent(
                event_id=str(uuid4()), ts=datetime(2026, 10, 1, 10, 1, tzinfo=timezone.utc),
                source="fixture", fingerprint="timeout",
                message="request timeout password=fixture-secret", level="error", service="api",
                duration_ms=120, request_status=503,
            )])
            self.ids.append(store.evidence(project["id"])[0]["id"])
        self.http = TestClient(self.app, base_url="http://127.0.0.1:18765")
        self.addCleanup(self.http.close)
        self.headers = {"Authorization": "Bearer " + store.control_token}
        self.binding = self.root / "project"
        (self.binding / ".logchat").mkdir(parents=True)
        (self.binding / ".logchat/local.toml").write_text(
            f'name="bound"\nproject_id="{self.project["id"]}"\n'
            'environment="dev"\napi_url="http://127.0.0.1:18765"\nsession_ref="synthetic"\n'
        )

    def bound_client_class(self):
        http = self.http
        token = self.app.state.store.control_token

        def relay(request):
            response = http.request(request.method, str(request.url), headers=dict(request.headers))
            return httpx.Response(response.status_code, json=response.json())

        class BoundClient(LocalLogchatClient):
            def __init__(self, project):
                super().__init__(project, transport=httpx.MockTransport(relay),
                                 credential_reader=lambda ref: json.dumps({"access_token": token}))
        return BoundClient

    async def test_status_separates_project_memory_and_committed_cursor_from_events(self):
        store = self.app.state.store
        own = self.project['id']
        # Conversation contents are deliberately not log evidence or status output.
        for project, count in ((self.project, 2), (self.other, 7)):
            conversation = store.create_conversation(project['id'], 'private title')['id']
            with store.connection(write=True) as connection:
                connection.execute('INSERT INTO conversation_context VALUES(?,?)',
                                   (conversation, '{"private":"conversation-secret"}'))
                for index in range(count):
                    connection.execute('''INSERT INTO conversation_messages
                        (id,project_id,conversation_id,request_id,role,content,created_at)
                        VALUES(?,?,?,?,?,?,?)''', (str(uuid4()), project['id'], conversation,
                        str(index), 'user', 'conversation-secret timeout', '2026-10-01T10:01:00Z'))
        first = store.list_sources(own)[0]['id']
        store.ingest(own, first, [], checkpoint=(None, '2026-10-01T12:00:00+02:00'))
        second = store.create_source(own, 'empty-window', 'dev', 'railway', None)['id']
        store.ingest(own, second, [], checkpoint=(None, '2026-10-01T09:30:00-01:00'))
        foreign = store.list_sources(self.other['id'])[0]['id']
        store.ingest(self.other['id'], foreign, [], checkpoint=(None, '2026-10-03T12:00:00Z'))
        path = f'/projects/{own}/status'
        self.assertEqual(self.http.get(path).status_code, 401)
        self.assertEqual(self.http.get(path, headers={'Authorization': 'Bearer ' +
            store.create_source(own, 'push-only', 'dev', 'push', None)['token']}).status_code, 401)
        response = self.http.get(path, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertTrue({'project', 'sources', 'chunks', 'recent_jobs', 'models'} <= status.keys())
        memory = status['memory']
        application = memory['application']
        self.assertEqual((status['chunks'], application['total_aggregates'], application['total_events']), (1, 1, 1))
        self.assertEqual(application['oldest_bucket_start'], application['latest_bucket_start'])
        self.assertEqual(application['bucket_seconds'], 900)
        self.assertIsNone(application['application_ttl_seconds'])
        self.assertFalse(application['raw_logs_stored'])
        self.assertEqual(application['query_source'], 'persisted_aggregates')
        self.assertEqual(application['retrieval'], 'native_lexical_not_vector')
        self.assertEqual(memory['conversation'], {'saved_conversations': 1, 'saved_messages': 2,
            'saved_checkpoints': 1, 'log_evidence': False, 'purpose': 'user_context_checkpoint'})
        collection = memory['collection']
        self.assertEqual(collection['sources_with_cursor'], 2)
        self.assertEqual(collection['oldest_committed_window_end'], '2026-10-01T10:00:00.000Z')
        self.assertEqual(collection['latest_committed_window_end'], '2026-10-01T10:30:00.000Z')
        self.assertIsNone(collection['last_provider_poll_at'])
        for secret in ('conversation-secret', 'fixture-secret', store.control_token, foreign, self.other['id']):
            self.assertNotIn(secret, response.text)
        async with Client(create_server(self.binding, client_factory=self.bound_client_class())) as connected:
            tool = next(tool for tool in (await connected.session.list_tools()).tools if tool.name == 'get_status')
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.open_world_hint)
            result = await connected.session.call_tool('get_status', {})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content['memory'], memory)

    async def test_status_empty_memory_has_zero_counts_and_unknown_boundaries(self):
        project = self.app.state.store.create_project('empty')
        response = self.http.get(f'/projects/{project["id"]}/status', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        memory = response.json()['memory']
        self.assertEqual(memory['application']['total_events'], 0)
        self.assertEqual(memory['application']['total_aggregates'], 0)
        self.assertIsNone(memory['application']['oldest_bucket_start'])
        self.assertIsNone(memory['application']['latest_bucket_start'])
        self.assertEqual(memory['conversation']['saved_messages'], 0)
        self.assertEqual(memory['conversation']['saved_checkpoints'], 0)
        self.assertEqual(memory['collection']['sources_with_cursor'], 0)
        self.assertIsNone(memory['collection']['latest_committed_window_end'])
        self.assertIsNone(memory['collection']['oldest_committed_window_end'])

    async def test_api_auth_project_isolation_and_complete_safe_memory(self):
        prefix = f'/projects/{self.project["id"]}/memories/'
        self.assertEqual(self.http.get(prefix + self.ids[0]).status_code, 401)
        source = self.app.state.store.create_source(self.project["id"], "ingest-only", "dev", "push", None)
        self.assertEqual(self.http.get(prefix + self.ids[0], headers={
            "Authorization": "Bearer " + source["token"]}).status_code, 401)
        response = self.http.get(prefix + self.ids[0], headers=self.headers)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        memory = result["memory"]
        self.assertEqual((memory["id"], memory["project_id"], memory["environment"], memory["service"]),
                         (self.ids[0], self.project["id"], "dev", "api"))
        self.assertEqual((memory["event_count"], memory["duration_count"], memory["duration_sum_ms"]), (1, 1, 120))
        self.assertEqual(memory["status_counts"], {"503": 1})
        self.assertIn("bucket_start", memory)
        self.assertIn("bucket_end", memory)
        self.assertEqual(memory["source_kind"], "push")
        self.assertEqual(result["provenance"]["snapshot"], False)
        for secret in ("fixture-secret", self.app.state.store.control_token, source["token"], "token_hash"):
            self.assertNotIn(secret, response.text)
        unknown = self.http.get(prefix + str(uuid4()), headers=self.headers)
        foreign = self.http.get(prefix + self.ids[1], headers=self.headers)
        self.assertEqual((unknown.status_code, foreign.status_code), (404, 404))
        self.assertEqual(unknown.json(), foreign.json())
        self.assertEqual(self.http.get(prefix + "not-an-id", headers=self.headers).status_code, 422)

    async def test_real_mcp_inspector_bound_client_and_readonly_metadata(self):
        async with Client(create_server(self.binding, client_factory=self.bound_client_class())) as connected:
            tools = (await connected.session.list_tools()).tools
            inspector = next(tool for tool in tools if tool.name == "inspect_memory")
            self.assertTrue(inspector.annotations.read_only_hint)
            self.assertFalse(inspector.annotations.open_world_hint)
            self.assertEqual(set(inspector.input_schema["properties"]), {"memory_id"})
            good = await connected.session.call_tool("inspect_memory", {"memory_id": self.ids[0]})
            self.assertFalse(good.is_error)
            self.assertEqual(good.structured_content["memory"]["project_id"], self.project["id"])
            for identifier in (self.ids[1], str(uuid4()), "../../settings/models"):
                rejected = await connected.session.call_tool("inspect_memory", {"memory_id": identifier})
                self.assertTrue(rejected.is_error)
                rendered = str(rejected.content)
                self.assertNotIn(self.app.state.store.control_token, rendered)
                self.assertNotIn("fixture-secret", rendered)

    async def test_real_mcp_context_is_bounded_readonly_and_project_bound(self):
        store = self.app.state.store
        own = store.create_conversation(self.project['id'], 'Synthetic investigation')['id']
        foreign = store.create_conversation(self.other['id'], 'Other investigation')['id']
        async with Client(create_server(self.binding, client_factory=self.bound_client_class())) as connected:
            tools = (await connected.session.list_tools()).tools
            for name in ('list_investigations', 'get_investigation_context'):
                tool = next(tool for tool in tools if tool.name == name)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.open_world_hint)
            good = await connected.session.call_tool('get_investigation_context', {'conversation_id': own})
            self.assertFalse(good.is_error)
            self.assertEqual(good.structured_content['turn_count'], 0)
            self.assertFalse(good.structured_content['metadata']['memory_is_evidence'])
            for identifier in (foreign, str(uuid4()), '../../settings/models'):
                result = await connected.session.call_tool('get_investigation_context', {'conversation_id': identifier})
                self.assertTrue(result.is_error)
                self.assertNotIn(store.control_token, str(result.content))
            listing = await connected.session.call_tool('list_investigations', {})
            self.assertFalse(listing.is_error)
            self.assertIn(own, str(listing.structured_content))
            self.assertNotIn(foreign, str(listing.structured_content))

    async def test_client_validation_and_package_version_provenance(self):
        with self.bound_client_class()(self.binding) as client:
            self.assertEqual(client.memory(self.ids[0])["memory"]["id"], self.ids[0])
            with self.assertRaises(LogchatError):
                client.memory("../other-project")
        server = create_server(self.binding, client_factory=self.bound_client_class())
        self.assertEqual(server._lowlevel_server.version, version("logchat"))
        with patch("logchat.mcp.version", return_value="0.2.0") as package_version:
            server = create_server(self.binding, client_factory=self.bound_client_class())
            self.assertEqual(server._lowlevel_server.version, "0.2.0")
            package_version.assert_called_once_with("logchat")


if __name__ == "__main__":
    unittest.main()
