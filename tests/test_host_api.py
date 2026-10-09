"""Host timeline is authenticated, bounded, scoped for app rows, and exposed through real MCP."""
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from uuid import uuid4
import httpx
import pytest
from mcp import Client
from cli.secrets import write_credential
from logchat.local.app import create_app
from logchat.local.client import LocalLogchatClient
from logchat.local.cli import save_binding
from logchat.mcp import create_server

@pytest.fixture
def anyio_backend():return "asyncio"

class FakeCapture:
    def __init__(self,store):self.started=False;self.stopped=False
    def start(self):self.started=True
    def stop(self):self.stopped=True
    def status(self):return {"running":self.started,"capabilities":{"network":{"status":"sampled"},"gpu":{"status":"unavailable"}},"dropped_events":0}
    def events(self,category="",limit=100,start=None,end=None):
        if category and category!="network":return []
        return [{"id":str(uuid4()),"timestamp":datetime.now(timezone.utc).isoformat(),"category":"network","level":"info","summary":"Observed network counters.","metrics":{"bytes_sent":42}}][:limit]

@pytest.mark.anyio
async def test_host_control_windows_categories_and_lifespan(tmp_path,monkeypatch):
    # This exercises a fake collector's lifecycle even when QA disables real host capture.
    monkeypatch.setenv("LOGCHAT_CAPTURE_HOST","1")
    with patch("logchat.local.telemetry.HostCapture",FakeCapture):app=create_app(tmp_path,18866)
    token=app.state.store.control_token
    async with app.router.lifespan_context(app):
        assert app.state.host_capture.started
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1:18866") as http:
            assert (await http.get("/host/timeline")).status_code==401
            http.headers["Authorization"]="Bearer "+token
            response=await http.get("/host/timeline?category=network&limit=1")
            assert len(response.json()["events"])==1
            assert response.json()["capture"]["capabilities"]["gpu"]["status"]=="unavailable"
            assert (await http.get("/host/timeline?category=packets")).status_code==422
            assert (await http.get("/host/timeline?limit=201")).status_code==422
            assert (await http.get("/host/timeline?start=2026-10-02T00:00:00Z")).status_code==422
            assert (await http.get("/host/timeline?start=2026-10-02T00:00:00&end=2026-10-03T00:00:00")).status_code==422
            assert (await http.get("/host/timeline",headers={"Origin":"https://unrelated.example"})).status_code==403
    assert app.state.host_capture.stopped

@pytest.mark.anyio
async def test_native_mcp_host_timeline_is_readonly_and_bound(tmp_path,monkeypatch):
    project=tmp_path/"project";project.mkdir();monkeypatch.setenv("LOGCHAT_SECRETS_DIR",str(tmp_path/"credentials"))
    project_id=str(uuid4());endpoint="http://127.0.0.1:18866"
    reference=write_credential('{"access_token":"synthetic"}',purpose="local_control",project_id=project_id,endpoint=endpoint)
    save_binding(project,{"project_id":project_id,"name":"fixture","environment":"dev","api_url":endpoint,"session_ref":reference,"source_id":str(uuid4()),"source_ref":str(uuid4())})
    seen=[]
    def transport(request):
        seen.append(request.url)
        assert request.headers["Authorization"]=="Bearer synthetic"
        return httpx.Response(200,json={"events":[],"capture":{"capabilities":{"gpu":{"status":"unavailable"}}},"scope":"test"})
    class TestClient(LocalLogchatClient):
        def __init__(self,project):super().__init__(project,transport=httpx.MockTransport(transport))
    async with Client(create_server(project,client_factory=TestClient)) as connected:
        listing=await connected.session.list_tools()
        tool=next(tool for tool in listing.tools if tool.name=="get_host_timeline")
        assert tool.annotations.read_only_hint and "project_id" not in tool.input_schema["properties"]
        result=await connected.session.call_tool("get_host_timeline",{"category":"gpu","limit":5})
        assert not result.is_error
    assert seen[-1].params["project_id"]==project_id
    assert seen[-1].params["category"]=="gpu"
