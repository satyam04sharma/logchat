import unittest
from pathlib import Path

from mcp import Client

from logchat.mcp import create_server


class FakeLogchatClient:
    calls = []
    def __init__(self, project): self.project = project
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def status(self): return {"project": {"name": "sample"}, "chunks": 3}
    def environments(self): return [{"id": "internal-id", "name": "dev"}, {"id": "other-id", "name": "prod"}]
    def ask(self, question, environments=None, **kwargs):
        self.calls.append(("ask", question, environments, kwargs)); return {"answer": "supported", "gaps": []}
    def compare(self, question, start, end, compare_start, compare_end, environments=None, **kwargs):
        self.calls.append(("compare", question, environments, kwargs)); return {"answer": "changed", "gaps": ["prod incomplete"]}


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_client_session_lists_and_calls_read_only_surface(self):
        FakeLogchatClient.calls.clear()
        server = create_server(Path("/bound/project"), client_factory=FakeLogchatClient)
        async with Client(server) as connected:
            session = connected.session
            tools = await session.list_tools()
            self.assertEqual({tool.name for tool in tools.tools}, {"get_status", "list_environments", "ask_logs", "compare_logs"})
            self.assertTrue(all(tool.annotations.read_only_hint for tool in tools.tools))
            self.assertTrue(all(tool.annotations.open_world_hint is False for tool in tools.tools))
            resources = await session.list_resources()
            self.assertEqual({str(resource.uri) for resource in resources.resources}, {"logchat://guide", "logchat://project"})
            prompts = await session.list_prompts()
            self.assertEqual({prompt.name for prompt in prompts.prompts}, {"investigate", "compare_periods"})

            status = await session.call_tool("get_status", {})
            self.assertEqual(status.structured_content["project"]["name"], "sample")
            asked = await session.call_tool("ask_logs", {"question": "why?", "environments": ["dev"]})
            self.assertEqual(asked.structured_content["answer"], "supported")
            compared = await session.call_tool("compare_logs", {
                "question": "what changed?", "start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z",
                "compare_start": "2026-08-01T00:00:00Z", "compare_end": "2026-08-02T00:00:00Z",
                "environments": ["dev", "prod"],
            })
            self.assertEqual(compared.structured_content["gaps"], ["prod incomplete"])
            guide = await session.read_resource("logchat://guide")
            self.assertIn("coverage gaps", guide.contents[0].text)
            self.assertIn("untrusted data", guide.contents[0].text)
            self.assertIn("local_model_preserved", guide.contents[0].text)
            self.assertIn("Tools cannot read arbitrary source files", guide.contents[0].text)
            self.assertIn("logchat login", guide.contents[0].text)
            project = await session.read_resource("logchat://project")
            self.assertIn('"dev"', project.contents[0].text)
            prompt = await session.get_prompt("compare_periods", {
                "question": "latency", "start": "this-start", "end": "this-end",
                "compare_start": "before-start", "compare_end": "before-end",
            })
            self.assertIn("compare_logs", prompt.messages[0].content.text)
        self.assertEqual(FakeLogchatClient.calls[0][:3], ("ask", "why?", ["dev"]))
        self.assertEqual(FakeLogchatClient.calls[1][:3], ("compare", "what changed?", ["dev", "prod"]))


if __name__ == "__main__": unittest.main()
