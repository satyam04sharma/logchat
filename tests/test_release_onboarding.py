"""Release onboarding and provider API contracts without cloud access or real models."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from typer.testing import CliRunner
from fastapi.testclient import TestClient

from cli.main import app as root_app
from logchat.local import cli, onboarding, installer
from logchat.local.app import create_app
from cli.secrets import secret_dir, read_credential


def test_installer_source_is_connected_only_after_selected_model_is_ready(tmp_path,monkeypatch):
    selected=AsyncMock(return_value={'status':'configured','provider':'ollama','endpoint':'http://localhost:11434','generation_model':'chosen','embedding':{}})
    connect=Mock(); start=Mock(return_value='http://127.0.0.1:18865')
    monkeypatch.setattr(installer,'prepare_installation',selected)
    monkeypatch.setattr(installer,'start',start)
    monkeypatch.setattr(onboarding,'onboard_source',connect)
    config=tmp_path/'source.json';config.write_text('{"container":"synthetic"}')
    result=CliRunner().invoke(root_app,['install', "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768", '--yes','--state-dir',str(tmp_path/'state'),'--source','docker','--source-config',str(config),'--project',str(tmp_path),'--port','18865'])
    assert result.exit_code==0,result.output
    selected.assert_awaited_once();start.assert_called_once()
    connect.assert_called_once_with(tmp_path/'state',18865,source='docker',project=tmp_path,config_path=config,interactive=False)


def test_invalid_source_and_no_start_are_rejected_before_model_setup(tmp_path,monkeypatch):
    selected=AsyncMock();monkeypatch.setattr(installer,'prepare_installation',selected)
    for flags in (['--source','unknown'],['--source','docker','--no-start']):
        result=CliRunner().invoke(root_app,['install', "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768", '--yes','--state-dir',str(tmp_path),*flags])
        assert result.exit_code==1
    selected.assert_not_awaited()


def test_config_files_cannot_supply_literal_tokens_or_symlinks(tmp_path):
    f=tmp_path/'config.json';f.write_text('{"token":"PRIVATE_TOKEN_CANARY"}')
    with pytest.raises(ValueError):onboarding.load_source_config(f)
    f.write_text('{}');link=tmp_path/'link.json';link.symlink_to(f)
    with pytest.raises(ValueError):onboarding.load_source_config(link)


def test_command_onboarding_does_not_start_an_arbitrary_command(tmp_path,capsys):
    result=onboarding.onboard_source(tmp_path,8765,source='command',project=tmp_path,interactive=False)
    assert result['status']=='command_instructions'
    assert '-- YOUR_APP_COMMAND' in capsys.readouterr().out


@pytest.fixture
def provider_api(tmp_path,monkeypatch):
    monkeypatch.setenv('LOGCHAT_SECRETS_DIR',str(tmp_path/'credentials'))
    application=create_app(tmp_path/'state',18765,capture_host=False)
    store=application.state.store
    store.rag_runtime=SimpleNamespace(model_profile=object())
    project=store.create_project('synthetic onboarding')
    source=store.create_source(project['id'],'provider','dev','vercel',None)
    http=TestClient(application,base_url='http://127.0.0.1:18765')
    config={'provider_project':'synthetic-provider','provider_environment':'production'}
    path=f"/projects/{project['id']}/sources/{source['id']}/provider"
    return application,http,project,source,path,config


def test_provider_token_is_control_authenticated_scoped_hidden_and_removed_on_disconnect(provider_api):
    application,http,project,source,path,config=provider_api
    body={'kind':'vercel','config':config,'token':'PRIVATE_TOKEN_CANARY'}
    assert http.post(path,json=body).status_code==401
    assert not secret_dir().exists()
    headers={'Authorization':'Bearer '+application.state.store.control_token}
    response=http.post(path,json=body,headers=headers)
    assert response.status_code==200,response.text
    assert 'PRIVATE_TOKEN_CANARY' not in response.text
    with application.state.store.connection() as conn:
        row=conn.execute('SELECT config_json FROM native_provider_sources').fetchone()
        stored=json.loads(row[0]);reference=stored['token_ref']
        assert 'PRIVATE_TOKEN_CANARY' not in row[0]
    assert read_credential(reference,purpose='native_provider',project_id=project['id'],source_id=source['id'],kind='vercel')=='PRIVATE_TOKEN_CANARY'
    with pytest.raises(RuntimeError):read_credential(reference,purpose='native_provider',project_id='foreign',source_id=source['id'],kind='vercel')
    assert 'PRIVATE_TOKEN_CANARY' not in http.get('/providers',headers=headers).text
    assert http.delete(path,headers=headers).status_code==200
    assert not (secret_dir()/(reference+'.json')).exists()


def test_wrong_source_kind_rejects_and_cleans_new_token(provider_api):
    application,http,project,source,path,config=provider_api
    headers={'Authorization':'Bearer '+application.state.store.control_token}
    response=http.post(path,json={'kind':'railway','config':{**config,'service':'api'},'token':'PRIVATE_CANARY'},headers=headers)
    assert response.status_code==422
    assert 'PRIVATE_CANARY' not in response.text
    assert not list(secret_dir().glob('*.json'))


def test_native_query_cli_returns_context_from_native_binding(monkeypatch,tmp_path):
    config={'project_id':'synthetic','environment_id':'dev-id'}
    monkeypatch.setattr(cli,'binding',lambda p:config)
    monkeypatch.setattr(cli,'directory_for',lambda *a:tmp_path)
    request=Mock(return_value={'output_kind':'retrieved_context','context_text':'Selected context','solution_generated':False})
    monkeypatch.setattr(cli,'api',request)
    result=CliRunner().invoke(cli.app,['ask','authentication context','--project',str(tmp_path),'--json'])
    assert result.exit_code==0,result.output
    assert 'retrieved_context' in result.output
    assert request.call_args.args[:3]==(tmp_path,'POST','/projects/synthetic/ask')
    assert request.call_args.args[3]['environment_ids']==['dev-id']


def test_token_prompt_is_hidden_and_no_token_cli_argument(tmp_path,monkeypatch):
    connect=Mock(return_value={'state':'configured'})
    monkeypatch.setattr(onboarding,'connect_provider_source',connect)
    monkeypatch.setattr(cli,'directory_for',lambda *a:tmp_path)
    result=CliRunner().invoke(cli.app,['connect-provider','vercel','--project',str(tmp_path),'--provider-project','fixture','--provider-environment','production','--prompt-token'],input='PRIVATE_TOKEN_CANARY\n')
    assert result.exit_code==0,result.output
    assert 'PRIVATE_TOKEN_CANARY' not in result.output
    assert connect.call_args.kwargs['token']=='PRIVATE_TOKEN_CANARY'
    rejected=CliRunner().invoke(cli.app,['connect-provider','vercel','--token','PRIVATE_TOKEN_CANARY'])
    assert rejected.exit_code!=0


def test_failed_source_setup_does_not_dump_traceback_or_provider_content(tmp_path,monkeypatch):
    selected=AsyncMock(return_value={'status':'configured','provider':'ollama','endpoint':'http://localhost:11434','generation_model':'chosen','embedding':{}})
    monkeypatch.setattr(installer,'prepare_installation',selected)
    monkeypatch.setattr(installer,'start',Mock(return_value='http://127.0.0.1:18865'))
    monkeypatch.setattr(onboarding,'onboard_source',Mock(side_effect=RuntimeError('PRIVATE_PROVIDER_CANARY')))
    result=CliRunner().invoke(root_app,['install', "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768", '--yes','--start','--state-dir',str(tmp_path),'--source','docker'])
    assert result.exit_code==1
    assert 'PRIVATE_PROVIDER_CANARY' not in result.output
    assert 'source connection did not finish' in result.output


def test_installer_cli_reconnect_and_multiple_new_sources_have_stable_identity(tmp_path,monkeypatch):
    from logchat.local import lifecycle
    monkeypatch.setenv('LOGCHAT_SECRETS_DIR',str(tmp_path/'credentials'))
    project=tmp_path/'app';project.mkdir()
    directory=tmp_path/'state';directory.mkdir()
    application=create_app(directory,18765,capture_host=False)
    application.state.store.rag_runtime=SimpleNamespace(model_profile=object(),status=lambda project_id:{})
    monkeypatch.setattr(application.state.store,'status',lambda project_id:{'project':{'id':project_id}})
    (directory/'rag.json').write_text('{}')
    http=TestClient(application,base_url='http://127.0.0.1:18765',headers={'Authorization':'Bearer '+application.state.store.control_token})
    def request(directory,method,path,body=None,**kwargs):
        r=http.request(method,path,json=body);r.raise_for_status();return r.json()
    monkeypatch.setattr(cli,'api',request)
    monkeypatch.setattr(cli,'managed_process',lambda *a:(object(),{'port':18765}))
    monkeypatch.setattr(cli,'local_url',lambda *a:'http://127.0.0.1:18765')
    monkeypatch.setattr(cli,'control_token',lambda *a:application.state.store.control_token)
    monkeypatch.setattr(lifecycle,'start',lambda *a:'http://127.0.0.1:18765')
    first=onboarding.connect_provider_source('docker',project,directory,18765,{'container':'fixture'})
    again=onboarding.connect_provider_source('docker',project,directory,18765,{'container':'fixture','cwd':str(project)})
    assert first['source_id']==again['source_id']
    second=onboarding.connect_provider_source('docker',project,directory,18765,{'container':'fixture2'},new_source=True)
    third=onboarding.connect_provider_source('docker',project,directory,18765,{'container':'fixture3'},new_source=True)
    assert len({first['source_id'],second['source_id'],third['source_id']})==3
    assert len(application.state.provider_capture.status())==3
