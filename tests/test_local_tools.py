"""Meaningful native lifecycle/discovery tests; no Docker daemon or real project logs."""
import json
import socket
from pathlib import Path
from unittest.mock import Mock,patch

import psutil
import pytest

from logchat.local.discovery import discover
from logchat.local.lifecycle import managed_process,start,state_directory,stop


def test_discovery_reads_listeners_not_arguments_environment_or_http(tmp_path):
    process=Mock();process.pid=12345;process.info={'name':'node'}
    process.uids.return_value=Mock(real=0)
    process.cwd.return_value=str(tmp_path)
    process.net_connections.return_value=[Mock(status=psutil.CONN_LISTEN,laddr=Mock(ip='127.0.0.1',port=3000)),Mock(status='ESTABLISHED',laddr=Mock(ip='127.0.0.1',port=44))]
    with patch('logchat.local.discovery.psutil.process_iter',return_value=[process]),patch('logchat.local.discovery.os.getuid',return_value=0):
        result=discover()
    assert len(result['projects'])==1 and result['projects'][0]['port']==3000
    assert result['projects'][0]['logging_connected'] is False
    process.cmdline.assert_not_called();process.environ.assert_not_called()


def test_discovery_denied_process_does_not_require_sudo():
    process=Mock();process.pid=12345;process.uids.side_effect=psutil.AccessDenied(pid=12345)
    with patch('logchat.local.discovery.psutil.process_iter',return_value=[process]):
        result=discover()
    assert result['projects']==[] and result['skipped_processes']==1


def test_port_collision_does_not_start_or_adopt_other_app(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        with patch('logchat.local.lifecycle.subprocess.Popen') as spawn:
            with pytest.raises(RuntimeError,match='occupied'):
                start(listener.getsockname()[1],tmp_path)
            spawn.assert_not_called()


def test_stale_pid_metadata_cannot_signal_unrelated_process(tmp_path):
    directory=state_directory(tmp_path)
    (directory/'manager.json').write_text(json.dumps({'pid':12345,'create_time':1,'port':8765}))
    process=Mock();process.create_time.return_value=2;process.cmdline.return_value=['python','other.py']
    with patch('logchat.local.lifecycle.psutil.Process',return_value=process):
        assert managed_process(directory)==(None,None)
        assert stop(directory) is False
    process.terminate.assert_not_called()


def test_reattach_previous_port_and_environment_has_distinct_capture_identity(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from logchat.local import cli
    from uuid import uuid4
    directory=tmp_path/'state';directory.mkdir()
    project=tmp_path/'app';project.mkdir()
    monkeypatch.setenv('LOGCHAT_SECRETS_DIR',str(tmp_path/'credentials'))
    project_id=str(uuid4());environments=[{'id':str(uuid4()),'name':'dev'},{'id':str(uuid4()),'name':'prod'}]
    sources=[]
    def request(directory,method,path,body=None):
        if path=='/projects':return [{'id':project_id,'name':'app','path':str(project)}]
        if path.endswith('/environments'):return environments
        if path.endswith('/sources'):
            assert not any(row['name']==body['name'] and row['environment']==body['environment'] for row in sources)
            source={**body,'id':str(uuid4()),'token':str(uuid4())};sources.append(source);return source
        return {}
    with patch.object(cli,'managed_process',return_value=(Mock(),{})),patch.object(cli,'local_url',return_value='http://127.0.0.1:8765'),patch.object(cli,'control_token',return_value='synthetic-control'),patch.object(cli,'api',side_effect=request),patch('logchat.local.lifecycle.locked',return_value=nullcontext()):
        bindings=[cli.attach_project(project,port=port,environment=environment,state_dir=directory) for port,environment in [(3000,'dev'),(3001,'dev'),(3000,'dev'),(3000,'prod'),(3000,'dev')]]
        assert len(set(value['source_id'] for value in bindings))==5
        assert cli.attach_project(project,port=3000,state_dir=directory)['source_id']==bindings[-1]['source_id']
        from cli.secrets import read_credential
        final=bindings[-1]
        assert read_credential(final['session_ref'],purpose='local_control',project_id=project_id,endpoint=final['api_url'])
        with pytest.raises(RuntimeError):read_credential(final['session_ref'],purpose='local_control',project_id=project_id,endpoint='http://127.0.0.1:9999')

        from cli.secrets import delete_credential
        delete_credential(final['session_ref'])
        repaired=cli.attach_project(project,port=3000,state_dir=directory)
        assert repaired['session_ref']!=final['session_ref']
        assert read_credential(repaired['session_ref'],purpose='local_control',project_id=project_id,endpoint=repaired['api_url'])


def test_daemon_start_does_not_resolve_modules_from_the_app_working_directory(tmp_path,monkeypatch):
    from logchat.local import lifecycle
    app=tmp_path/'app';app.mkdir()
    (app/'logchat').mkdir();(app/'logchat/__init__.py').write_text('raise RuntimeError("wrong application package")')
    monkeypatch.chdir(app)
    monkeypatch.setattr(lifecycle,'ensure_fresh_rag',lambda directory:None)
    monkeypatch.setattr(lifecycle,'managed_process',lambda directory:(None,None))
    spawn=Mock(side_effect=OSError('synthetic process stop before execution'))
    monkeypatch.setattr(lifecycle.subprocess,'Popen',spawn)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    with pytest.raises(OSError):lifecycle.start(port,tmp_path/'state')
    assert spawn.call_args.kwargs['cwd']!=app
    assert (spawn.call_args.kwargs['cwd']/'logchat/local/server.py').is_file()


def test_generated_mcp_command_cannot_be_shadowed_by_application_cli(tmp_path,monkeypatch):
    import subprocess
    from typer.testing import CliRunner
    from logchat.local import cli
    project=tmp_path/'app';project.mkdir()
    (project/'cli').mkdir();(project/'cli/__init__.py').write_text('')
    (project/'cli/main.py').write_text('print("SHADOW_APPLICATION_CLI")')
    monkeypatch.setattr(cli,'binding',lambda p:{'project_id':'synthetic'})
    result=CliRunner().invoke(cli.app,['integration','--project',str(project)])
    assert result.exit_code==0,result.output
    config=json.loads(result.output)['mcpServers']['logchat']
    checked=subprocess.run([config['command'],*config['args'],'--help'],cwd=project,capture_output=True,text=True,timeout=20)
    assert checked.returncode==0,checked.stderr
    assert 'SHADOW_APPLICATION_CLI' not in checked.stdout
    assert '--project' in checked.stdout
