from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from logchat.local.app import create_app


def test_model_selection_control_auth_and_safe_errors(tmp_path,monkeypatch):
    from logchat.local import shared_settings
    application=create_app(tmp_path,8807,capture_host=False);store=application.state.store
    listing=AsyncMock(return_value={'models':[{'name':'chosen:7b'}]})
    selection=AsyncMock(return_value={'generation_model':'chosen:7b','endpoint':'http://127.0.0.1:11434'})
    monkeypatch.setattr(shared_settings,'list_models',listing);monkeypatch.setattr(shared_settings,'select_model',selection)
    c=TestClient(application,base_url='http://127.0.0.1:8807');h={'Authorization':'Bearer '+store.control_token}
    assert c.get('/settings/models/available').status_code==401
    assert c.put('/settings/shared-model',json={'model':'chosen:7b'}).status_code==401
    p=store.create_project('scope');source=store.create_source(p['id'],'push','dev','push',8000)
    assert c.put('/settings/shared-model',headers={'Authorization':'Bearer '+source['token']},json={'model':'chosen:7b'}).status_code==401
    listing.assert_not_called();selection.assert_not_called()
    assert c.get('/settings/models/available',headers=h).json()['models'][0]['name']=='chosen:7b'
    assert c.put('/settings/shared-model',headers=h,json={'model':'chosen:7b'}).status_code==200
    assert selection.call_args.kwargs=={'endpoint':'http://127.0.0.1:11434','model':'chosen:7b'}
    selection.side_effect=shared_settings.SettingsError(409,'Log preparation is running. Retry later.')
    r=c.put('/settings/shared-model',headers=h,json={'model':'chosen:7b'})
    assert r.status_code==409 and r.json()['detail']=='Log preparation is running. Retry later.'
    assert c.put('/settings/shared-model',headers=h,json={'model':'chosen:7b','stage_override':'other'}).status_code==422


def test_unavailable_connection_check_reports_not_ready(tmp_path,monkeypatch):
    from pipeline.models import ModelUnavailable
    application=create_app(tmp_path,8807,capture_host=False);store=application.state.store
    store.save_settings('ollama','http://127.0.0.1:11434','missing-model',None,None)
    async def missing(self):raise ModelUnavailable('provider error contains unrelated private detail')
    monkeypatch.setattr('pipeline.models.LocalModels.installed',missing)
    c=TestClient(application,base_url='http://127.0.0.1:8807')
    r=c.post('/settings/models/check',headers={'Authorization':'Bearer '+store.control_token})
    assert r.status_code==200 and r.json()['ok'] is False
    assert 'private detail' not in r.text
