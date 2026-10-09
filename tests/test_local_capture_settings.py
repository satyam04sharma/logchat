import json
from unittest.mock import Mock
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from cli.main import app as cli
from logchat.local.app import create_app
from logchat.local import installer
from logchat.local.lifecycle import ensure_fresh_rag


def test_capture_only_installer_does_not_probe_or_create_model_profile(tmp_path,monkeypatch):
    probe=Mock(side_effect=AssertionError('must not discover models'))
    monkeypatch.setattr(installer,'LocalModels',probe)
    monkeypatch.setattr(installer,'managed_process',Mock(return_value=(None,None)))
    result=CliRunner().invoke(cli,['install','--capture-only','--yes','--state-dir',str(tmp_path),
        '--raw-limit-mb','2','--raw-retention-hours','1'])
    assert result.exit_code==0,result.output
    config=json.loads((tmp_path/'capture.json').read_text())
    assert config['mode']=='retain_until_summarized' and config['max_bytes']==2*1048576
    assert not (tmp_path/'rag.json').exists()
    ensure_fresh_rag(tmp_path)
    probe.assert_not_called()


def test_raw_only_api_auth_settings_intake_and_no_false_search(tmp_path):
    from logchat.local.raw_capture import save_capture_policy
    save_capture_policy(tmp_path,mode='retain_until_summarized')
    application=create_app(tmp_path,8806,capture_host=False)
    store=application.state.store
    with TestClient(application,base_url='http://127.0.0.1:8806') as client:
        h={'Authorization':'Bearer '+store.control_token}
        assert client.get('/settings/capture').status_code==401
        assert client.put('/settings/capture',json={'mode':'summary_only'}).status_code==401
        assert client.put('/settings/capture',headers=h,json={'mode':'retain_until_summarized','max_bytes':1}).status_code==422
        p=store.create_project('pending');source=store.create_source(p['id'],'push','dev','push',8000)
        sh={'Authorization':'Bearer '+source['token']}
        payload={'source_id':source['id'],'events':[{'event_id':'one','timestamp':'2026-10-04T22:00:00Z','message':'Unique original retained temporarily','level':'error','service':'demo'}]}
        response=client.post(f"/projects/{p['id']}/events",headers=sh,json=payload)
        assert response.status_code==200,response.text
        assert response.json()['original_events_stored'] is True
        s=client.get('/settings/capture',headers=h).json()
        assert s['status']['pending_events']==1 and s['model_configured'] is False
        assert client.get('/settings/models',headers=h).json()['provider']=='unconfigured'
        search=client.get(f"/projects/{p['id']}/search",headers=h).json()
        assert not search['evidence'] and search['pending_not_searchable']
        status=client.get(f"/projects/{p['id']}/status",headers=h).json()
        assert status['memory']['application']['full_event_text_stored'] is True
        environment=store.list_environments(p['id'])[0]['id']
        assert client.post(f"/projects/{p['id']}/ask",headers=h,json={'question':'find original','environment_ids':[environment]}).status_code==503
        changed=client.put('/settings/capture',headers=h,json={'mode':'summary_only'})
        assert changed.status_code==200 and changed.json()['status']['pending_events']==1
        payload['events'][0]['event_id']='two'
        assert client.post(f"/projects/{p['id']}/events",headers=sh,json=payload).status_code==503
        assert client.get('/settings/capture',headers=h).json()['status']['pending_events']==1


def test_enable_capture_from_existing_app_and_file_connection(tmp_path):
    application=create_app(tmp_path,8806,capture_host=False)
    store=application.state.store
    with TestClient(application,base_url='http://127.0.0.1:8806') as client:
        h={'Authorization':'Bearer '+store.control_token}
        assert client.put('/settings/capture',headers=h,json={'mode':'retain_until_summarized'}).status_code==200
        p=store.create_project('file');source=store.create_source(p['id'],'file','dev','push',8000)
        log=tmp_path/'source.log';log.write_text('A local original event\n')
        response=client.post(f"/projects/{p['id']}/sources/{source['id']}/file",headers=h,json={'path':str(log),'from_start':True})
        assert response.status_code==200,response.text
        application.state.file_capture.poll()
        assert client.get('/settings/capture',headers=h).json()['status']['pending_events']==1
