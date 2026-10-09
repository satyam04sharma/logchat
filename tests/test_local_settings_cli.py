from unittest.mock import Mock
from typer.testing import CliRunner
from cli.main import app
from logchat.local import cli


def test_settings_show_shares_api_and_local_alias(tmp_path,monkeypatch):
    api=Mock(return_value={'configured':True});monkeypatch.setattr(cli,'api',api)
    for prefix in (['settings'],['local','settings']):
        r=CliRunner().invoke(app,[*prefix,'show','--state-dir',str(tmp_path)])
        assert r.exit_code==0,r.output
        assert '"capture"' in r.output and '"ui"' in r.output and '"model"' in r.output
    assert {call.args[2] for call in api.call_args_list}=={'/settings/models','/settings/capture','/settings/ui'}


def test_capture_preserves_unspecified_limits_and_validates_mode(tmp_path,monkeypatch):
    api=Mock(side_effect=[{'max_bytes':8*1048576,'retention_seconds':7200},{'mode':'summary_only'}]);monkeypatch.setattr(cli,'api',api)
    r=CliRunner().invoke(app,['settings','capture','--mode','summary_only','--state-dir',str(tmp_path)])
    assert r.exit_code==0,r.output
    assert api.call_args.args[3]=={'mode':'summary_only','max_bytes':8*1048576,'retention_seconds':7200}
    api.reset_mock()
    assert CliRunner().invoke(app,['settings','capture','--mode','invalid']).exit_code!=0
    api.assert_not_called()


def test_model_and_visibility_cli_use_shared_endpoints(tmp_path,monkeypatch):
    api=Mock(side_effect=[{'base_url':'http://127.0.0.1:11434'},{'generation_model':'chosen:7b'},{'environments_enabled':False}]);monkeypatch.setattr(cli,'api',api)
    r=CliRunner().invoke(app,['settings','model','--model','chosen:7b','--state-dir',str(tmp_path)])
    assert r.exit_code==0,r.output
    assert api.call_args.args[2:] == ('/settings/shared-model',{'model':'chosen:7b','endpoint':'http://127.0.0.1:11434'})
    assert api.call_args.kwargs=={'timeout':180}
    r=CliRunner().invoke(app,['settings','ui','--no-environments','--state-dir',str(tmp_path)])
    assert r.exit_code==0,r.output
    assert api.call_args.args[3]=={'environments_enabled':False}


def test_models_list_and_check(tmp_path,monkeypatch):
    api=Mock(return_value={'ok':True});monkeypatch.setattr(cli,'api',api)
    for action,path in [('models','/settings/models/available'),('check','/settings/models/check')]:
        r=CliRunner().invoke(app,['settings',action,'--state-dir',str(tmp_path)])
        assert r.exit_code==0,r.output
        assert api.call_args.args[2]==path
