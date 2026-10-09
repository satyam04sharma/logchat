import os
import runpy
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from cli.main import _docker_environment
from cli import runtime


def test_repeated_setup_preserves_protected_credentials(tmp_path):
    root=Path(__file__).resolve().parents[1]
    script=tmp_path/'install/scripts/setup.py';script.parent.mkdir(parents=True)
    shutil.copy2(root/'scripts/setup.py',script)
    with patch.object(Path,'home',return_value=tmp_path/'user'):
        runpy.run_path(str(script),run_name='__main__')
        settings=tmp_path/'install/.logchat/.secrets'
        before=settings.read_bytes()
        with pytest.raises(SystemExit) as result:runpy.run_path(str(script),run_name='__main__')
        assert result.value.code==0
        assert settings.read_bytes()==before
        assert settings.stat().st_mode & 0o777==0o600


def test_upgrade_refreshes_runtime_assets_without_changing_installation_state(tmp_path):
    import logchat
    package=tmp_path/'site/logchat';bundle=package/'_stack';bundle.mkdir(parents=True)
    (bundle/'compose.yaml').write_text('first-version')
    with patch.dict(os.environ,{},clear=True),patch.object(Path,'home',return_value=tmp_path/'user'), \
         patch.object(runtime,'__file__',str(tmp_path/'site/cli/runtime.py')), \
         patch.object(logchat,'__file__',str(package/'__init__.py')),patch.object(runtime,'version',return_value='1'):
        first=runtime.prepare_runtime()
        secrets=first/'.logchat/.secrets';secrets.parent.mkdir();secrets.write_text('protected-canary')
    (bundle/'compose.yaml').write_text('second-version')
    with patch.dict(os.environ,{},clear=True),patch.object(Path,'home',return_value=tmp_path/'user'), \
         patch.object(runtime,'__file__',str(tmp_path/'site/cli/runtime.py')), \
         patch.object(logchat,'__file__',str(package/'__init__.py')),patch.object(runtime,'version',return_value='2'):
        second=runtime.prepare_runtime()
    assert first==second
    assert secrets.read_text()=='protected-canary'
    assert (second/'compose.yaml').read_text()=='second-version'


def test_remote_daemon_rejected_and_local_endpoint_pinned():
    with patch.dict(os.environ,{'DOCKER_HOST':'ssh://remote'},clear=True):
        with pytest.raises(typer.BadParameter):_docker_environment()
    result=type('Result',(),{'returncode':0,'stdout':'unix:///tmp/local-docker.sock\n'})()
    with patch.dict(os.environ,{'DOCKER_CONTEXT':'local-desktop','DOCKER_HOST':'tcp://remote:2375'},clear=True), \
         patch('cli.main.subprocess.run',return_value=result) as run:
        env=_docker_environment()
        assert 'local-desktop' in run.call_args.args[0]
        assert 'DOCKER_CONTEXT' not in env
        assert env['DOCKER_HOST']=='unix:///tmp/local-docker.sock'
