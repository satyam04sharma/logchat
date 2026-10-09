import unittest
from contextlib import contextmanager
from unittest.mock import patch

from api.mcp_connections import _connection, _public
from api.settings import _public as public_model, _retained_credential


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, owner_rows):
        self.owner_rows = owner_rows
        self.claimed_owner = None

    def execute(self, query, params=()):
        # The production database context installs this owner's JWT claims before this query;
        # emulate the resulting RLS view, including an attempted lookup of another owner's id.
        row = self.owner_rows.get(self.claimed_owner)
        requested = str(params[0]) if params else None
        if row and requested == str(row["id"]):
            return FakeResult(row)
        return FakeResult(None)


class SettingsTests(unittest.TestCase):
    def test_saved_model_key_is_only_retained_for_same_https_endpoint(self):
        previous = {"provider": "openai_compatible", "base_url": "https://one.example/v1",
                    "api_key_ref": "credential-reference"}
        self.assertEqual(_retained_credential(previous, "openai_compatible", "https://one.example/v1"),
                         "credential-reference")
        self.assertIsNone(_retained_credential(previous, "openai_compatible", "https://two.example/v1"))
        self.assertIsNone(_retained_credential(previous, "openai_compatible", "http://localhost:9000/v1"))

    def test_public_shapes_redact_credentials(self):
        model = public_model({"provider": "openai_compatible", "chat_model": "chat", "base_url": "https://example.com/v1",
                              "api_key_ref": "credential-reference"})
        self.assertTrue(model["has_api_key"])
        self.assertNotIn("api_key_ref", model)
        mcp = _public({"id": "00000000-0000-0000-0000-000000000001", "name": "docs", "url": "https://example.com/mcp",
                       "transport": "streamable_http", "enabled": True, "api_key_ref": "credential-reference", "created_at": "now"})
        self.assertTrue(mcp["has_api_key"])
        self.assertNotIn("api_key_ref", mcp)

    def test_connection_lookup_is_owner_scoped(self):
        first_id = "00000000-0000-0000-0000-000000000001"
        second_id = "00000000-0000-0000-0000-000000000002"
        rows = {
            "owner-a": {"id": first_id, "name": "a", "url": "https://a.example/mcp", "transport": "streamable_http",
                        "enabled": True, "api_key_ref": None, "created_at": "now"},
            "owner-b": {"id": second_id, "name": "b", "url": "https://b.example/mcp", "transport": "streamable_http",
                        "enabled": True, "api_key_ref": None, "created_at": "now"},
        }
        fake = FakeConnection(rows)

        @contextmanager
        def scoped_database(owner_id):
            fake.claimed_owner = owner_id
            yield fake

        with patch("api.mcp_connections.database", scoped_database):
            self.assertEqual(str(_connection("owner-a", first_id)["id"]), first_id)
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                _connection("owner-a", second_id)
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
