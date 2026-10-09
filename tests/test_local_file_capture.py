"""Real durable queue + explicit files; embedding transport is mocked here."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from logchat.local.app import create_app
from logchat.local.cli import app
from logchat.local.files import FileCapture
from logchat.local.rag_runtime import configure
from logchat.local.store import LocalStore
from logchat.rag.contracts import EmbeddingSpec
from logchat.rag.embeddings import OllamaEmbeddingProvider


class Embeddings:
    spec=EmbeddingSpec('ollama','mock','mock-digest',8)
    async def embed(self,texts,**kwargs):return [(1,.1,.2,.3,.4,.5,.6,.7) for _ in texts]


@pytest_asyncio.fixture
async def configured(tmp_path,monkeypatch):
    async def create(**kwargs):return Embeddings()
    monkeypatch.setattr(OllamaEmbeddingProvider,'create',create)
    await configure(tmp_path/'state',chat_model="mock-generation", model='mock',dimensions=8)
    store=LocalStore(tmp_path/'state');project=store.create_project('files')
    source=store.create_source(project['id'],'app file','dev','push',8000)
    return store,project,source


def queued(store):
    with store.rag_runtime.backend.connect() as conn:
        return [json.loads(row[0]) for row in conn.execute('SELECT payload FROM rag_schedule_jobs')]


@pytest.mark.asyncio
async def test_initial_tail_partial_lines_restart_and_queue_commit_replay(configured,tmp_path,monkeypatch):
    store,project,source=configured
    path=tmp_path/'app.log';path.write_text('old history\n')
    capture=FileCapture(store);capture.configure(project['id'],source['id'],path)
    capture.poll();assert not queued(store)
    with path.open('a') as out:out.write('Database update delayed by contention')
    capture.poll();assert not queued(store)
    with path.open('a') as out:out.write('; will retry\n')
    # Simulate queue publication followed by a crash before saving file progress.
    previous=store.rag_runtime.ingest
    def crash(*args):
        previous(*args);raise RuntimeError('synthetic crash')
    monkeypatch.setattr(store.rag_runtime,'ingest',crash)
    capture.poll();assert len(queued(store))==1
    reopened=LocalStore(store.state_dir);watcher=FileCapture(reopened)
    watcher.poll();assert len(queued(reopened))==1
    assert watcher.status(project['id'])['sources'][0]['offset']==path.stat().st_size
    with path.open('a') as out:out.write('Another distinct retained event\n')
    watcher.poll();assert len(queued(reopened))==2
    path.rename(tmp_path/'app.log.1');path.write_text('Rotated file incident\n')
    real_ingest=reopened.rag_runtime.ingest
    def rotated_crash(*args):
        real_ingest(*args);raise RuntimeError('synthetic rotation crash')
    monkeypatch.setattr(reopened.rag_runtime,'ingest',rotated_crash)
    watcher.poll();assert len(queued(reopened))==3
    recovered=LocalStore(store.state_dir)
    FileCapture(recovered).poll();assert len(queued(recovered))==3


@pytest.mark.asyncio
async def test_rotation_truncation_redaction_and_bounded_oversize(configured,tmp_path):
    store,project,source=configured
    path=tmp_path/'app.log';path.write_text('Credential expired token=private_file_canary\n')
    capture=FileCapture(store);capture.configure(project['id'],source['id'],path,from_start=True)
    capture.poll();assert 'private_file_canary' not in json.dumps(queued(store))
    path.rename(tmp_path/'app.log.1');path.write_text('Storage attempt failed\n')
    capture.poll();assert len(queued(store))==2
    path.write_text('short\n');capture.poll();assert len(queued(store))==3
    with path.open('a') as out:out.write('x'*25000+'\nValid event after oversized line\n')
    capture.poll();assert len(queued(store))==4
    row=capture.status(project['id'])['sources'][0]
    assert row['gaps']==3 and row['state']=='watching'
    capture.disable(project['id'],source['id'])
    with path.open('a') as out:out.write('Not captured while disconnected\n')
    capture.poll();assert len(queued(store))==4
    capture.configure(project['id'],source['id'],path)
    capture.poll();assert len(queued(store))==5


@pytest.mark.asyncio
async def test_authenticated_configuration_scope_and_symlink_rejection(configured,tmp_path):
    store,project,source=configured
    path=tmp_path/'app.log';path.write_text('test\n')
    application=create_app(store.state_dir,8772,capture_host=False)
    client=TestClient(application,base_url='http://127.0.0.1:8772')
    route=f"/projects/{project['id']}/sources/{source['id']}/file"
    assert client.post(route,json={'path':str(path)}).status_code==401
    headers={'Authorization':'Bearer '+store.control_token}
    assert client.post(route,json={'path':str(path)},headers=headers).status_code==200
    foreign=store.create_project('foreign')
    assert client.post(f"/projects/{foreign['id']}/sources/{source['id']}/file",json={'path':str(path)},headers=headers).status_code==422
    link=tmp_path/'link.log';link.symlink_to(path)
    with pytest.raises(ValueError,match='symlink'):FileCapture.open_file(link)
    assert client.delete(route,headers=headers).status_code==200
    assert application.state.file_capture.status(project['id'])['sources'][0]['enabled']==0


def test_connect_port_only_and_conflicting_modes_fail_before_start(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('must not start')
    monkeypatch.setattr('logchat.local.cli.start',forbidden)
    runner=CliRunner()
    for args in [
        ['connect','--project',str(tmp_path),'--port','8000'],
        ['connect','--project',str(tmp_path),'--port','8000','--logchat-port','8000','--','echo','hello'],
        ['connect','--project',str(tmp_path),'--log-file','app.log','--','echo','hello'],
    ]:
        result=runner.invoke(app,args)
        assert result.exit_code==2,result.output


def test_connect_preserves_wrapped_command_exit_status(tmp_path,monkeypatch):
    import typer
    (tmp_path/'rag.json').write_text('{}')
    monkeypatch.setattr('logchat.local.cli.managed_process',lambda directory:(None,None))
    monkeypatch.setattr('logchat.local.cli.start',lambda *args:None)
    def finish(*args):raise typer.Exit(0)
    monkeypatch.setattr('logchat.local.cli.run_command',finish)
    result=CliRunner().invoke(app,['connect','--project',str(tmp_path),'--state-dir',str(tmp_path),'--','echo','test'])
    assert result.exit_code==0,result.output


@pytest.mark.asyncio
async def test_tail_skips_existing_incomplete_line_and_backpressure_preserves_cursor(configured,tmp_path,monkeypatch):
    store,project,source=configured
    path=tmp_path/'app.log';path.write_text('existing unfinished')
    capture=FileCapture(store);capture.configure(project['id'],source['id'],path)
    with path.open('a') as out:out.write(' suffix\nA complete new record\n')
    capture.poll();assert len(queued(store))==1
    assert 'suffix' not in json.dumps(queued(store))
    before=capture.status(project['id'])['sources'][0]['offset']
    with path.open('a') as out:out.write('Next event waits for capacity\n')
    monkeypatch.setattr(store.rag_runtime.scheduler,'status',lambda identity:{'jobs':{'failed':64}})
    capture.poll()
    row=capture.status(project['id'])['sources'][0]
    assert row['state']=='backpressure' and row['offset']==before
    assert len(queued(store))==1


@pytest.mark.asyncio
async def test_push_processing_diagnostics_are_allowlisted(configured, monkeypatch):
    from pipeline.models import ModelUnavailable
    store, project, source = configured
    application = create_app(store.state_dir, 8772, capture_host=False)
    client = TestClient(application, base_url='http://127.0.0.1:8772')
    def fail(*args):
        error = ModelUnavailable('private-model-response-canary')
        error.category, error.phase = 'compact_invalid_output', 'compact_summary'
        raise error
    monkeypatch.setattr(application.state.store, 'ingest', fail)
    response = client.post(f"/projects/{project['id']}/events",
        json={'source_id': source['id'], 'events': [{'message': 'Synthetic failure'}]},
        headers={'Authorization': 'Bearer ' + source['token']})
    assert response.status_code == 503
    assert response.headers['X-Logchat-Processing-Category'] == 'compact_invalid_output'
    assert response.headers['X-Logchat-Processing-Phase'] == 'compact_summary'
    assert 'private-model-response-canary' not in response.text + str(response.headers)


def test_unknown_processing_diagnostics_never_echo_exception():
    from logchat.local.raw_capture import processing_failure
    error = RuntimeError('private-log-and-model-canary')
    error.category = error.phase = 'private-log-and-model-canary'
    assert processing_failure(error) == ('compact_processing_unavailable', 'intake')
