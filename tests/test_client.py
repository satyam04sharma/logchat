import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from logchat import LogchatClient, LogchatError


class ClientTests(unittest.TestCase):
    def project(self):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name)
        (root / ".logchat").mkdir()
        project_id = str(uuid4())
        (root / ".logchat/config.toml").write_text(
            f'name = "sample"\nproject_id = "{project_id}"\nenvironment = "dev"\nsession_ref = "session-reference"\n'
        )
        self.addCleanup(temporary.cleanup)
        return root, project_id

    def test_context_client_scopes_project_and_resolves_environment_names(self):
        root, project_id = self.project(); requests = []
        dev_id = str(uuid4())
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-token")
            requests.append(request)
            if request.url.path.endswith("/environments"):
                return httpx.Response(200, json=[{"id": dev_id, "name": "dev"}])
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"project": {"name": "sample"}, "chunks": 2})
            return httpx.Response(200, json={"answer": "evidence", "gaps": []})
        with LogchatClient(root, api_url="http://localhost:8081", transport=httpx.MockTransport(handler),
                           credential_reader=lambda ref: json.dumps({"access_token": "test-token"})) as client:
            self.assertEqual(client.status()["chunks"], 2)
            result = client.compare("what changed?", "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
                                    "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z")
        self.assertEqual(result["answer"], "evidence")
        body = json.loads(requests[-1].content)
        self.assertEqual(requests[-1].url.path, f"/projects/{project_id}/ask")
        self.assertEqual(body["environment_ids"], [dev_id])
        self.assertEqual(body["compare_start"], "2026-08-01T00:00:00Z")

    def test_requires_context_and_complete_windows(self):
        root, _ = self.project()
        client = LogchatClient(root, api_url="http://127.0.0.1:8080",
                               credential_reader=lambda ref: json.dumps({"access_token": "token"}))
        with self.assertRaises(LogchatError): client.status()
        with client:
            with self.assertRaises(LogchatError): client.ask("question", start="2026-01-01T00:00:00Z")
            with self.assertRaises(LogchatError): client.compare("question", "", "end", "before", "after")

    def test_rejects_remote_api_and_sanitizes_backend_errors(self):
        root, _ = self.project()
        with self.assertRaises(LogchatError): LogchatClient(root, api_url="https://logs.example.com")
        transport = httpx.MockTransport(lambda request: httpx.Response(500, json={"detail": "password=backend-secret"}))
        client = LogchatClient(root, api_url="http://127.0.0.1:8080", transport=transport,
                               credential_reader=lambda ref: json.dumps({"access_token": "token"}))
        with client, self.assertRaises(LogchatError) as caught: client.status()
        self.assertNotIn("backend-secret", str(caught.exception))

    def test_project_path_does_not_change_global_working_directory(self):
        root, _ = self.project(); before = Path.cwd()
        LogchatClient(root, api_url="http://127.0.0.1:8080",
                      credential_reader=lambda ref: json.dumps({"access_token": "token"}))
        self.assertEqual(Path.cwd(), before)

    def test_default_api_port_comes_from_packaged_runtime_directory(self):
        root, _ = self.project()
        with tempfile.TemporaryDirectory() as runtime:
            settings = Path(runtime, ".logchat"); settings.mkdir()
            Path(settings, ".secrets").write_text("API_PORT=8081\n")
            with patch.dict(os.environ, {}, clear=True), patch("logchat.client.runtime_dir", return_value=Path(runtime)):
                client = LogchatClient(root, credential_reader=lambda ref: json.dumps({"access_token": "token"}))
        self.assertEqual(client.api_url, "http://127.0.0.1:8081")

    def test_expired_session_error_instructs_login_without_backend_detail(self):
        root, _ = self.project()
        transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "token backend-secret"}))
        client = LogchatClient(root, api_url="http://127.0.0.1:8080", transport=transport,
                               credential_reader=lambda ref: json.dumps({"access_token": "expired"}))
        with client, self.assertRaises(LogchatError) as caught: client.status()
        self.assertIn("logchat login", str(caught.exception))
        self.assertNotIn("backend-secret", str(caught.exception))


if __name__ == "__main__": unittest.main()
