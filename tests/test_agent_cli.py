import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from typer.testing import CliRunner
from cli.main import app


def test_mcp_config_uses_explicit_project_and_contains_no_session_values():
    with tempfile.TemporaryDirectory(prefix='logchat-agent ') as temp:
        project=Path(temp);(project/'.logchat').mkdir()
        (project/'.logchat/config.toml').write_text('session_ref="secret-canary"\n')
        result=CliRunner().invoke(app,['mcp','config','--project',temp])
        assert result.exit_code==0
        value=json.loads(result.stdout)
        assert value['mcpServers']['logchat']['args'][-1]==str(project.resolve())
        assert 'secret-canary' not in result.stdout


def test_setup_does_not_start_services_when_retention_is_missing():
    with patch('cli.main.up') as up:
        result=CliRunner().invoke(app,['setup','--docker','--container','my-app'])
        assert result.exit_code!=0
        up.assert_not_called()


def test_doctor_reports_expired_session_without_secret_details():
    with patch('cli.main.config_path') as path,patch('cli.main.request') as request:
        path.return_value.exists.return_value=True
        request.side_effect=[{'status':'ready','checks':{}},RuntimeError('sensitive detail')]
        result=CliRunner().invoke(app,['doctor'])
        assert result.exit_code==1
        assert 'logchat login' in result.stdout
        assert 'sensitive detail' not in result.stdout


def test_mcp_startup_error_does_not_pollute_protocol_stdout():
    with tempfile.TemporaryDirectory() as project:
        result=CliRunner().invoke(app,['mcp','serve','--project',project])
        assert result.exit_code==1
        assert result.stdout==''
        assert 'initialized logchat project' in result.stderr


def test_mcp_config_retains_validated_local_api_override():
    with tempfile.TemporaryDirectory() as temp,patch.dict('os.environ',{'LOGCHAT_API_URL':'http://127.0.0.1:9090'}):
        project=Path(temp);(project/'.logchat').mkdir();(project/'.logchat/config.toml').write_text('session_ref="canary"')
        result=CliRunner().invoke(app,['mcp','config','--project',temp])
        assert result.exit_code==0
        entry=json.loads(result.stdout)['mcpServers']['logchat']
        assert entry['env']['LOGCHAT_API_URL']=='http://127.0.0.1:9090'
        assert 'canary' not in result.stdout
