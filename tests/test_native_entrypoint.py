"""The public command and generated integrations must select the native runtime."""
from click import unstyle
from typer.testing import CliRunner
from cli.native import app


def test_public_command_exposes_native_workflow_without_historical_stack_commands():
    result = CliRunner().invoke(app, ['--help'], color=False)
    assert result.exit_code == 0
    for command in ('install', 'start', 'local', 'settings'):
        assert command in result.stdout
    for command in ('login', 'init', 'doctor'):
        assert all(row.name != command for row in app.registered_commands)
    assert {row.name for row in app.registered_groups} == {'local', 'settings'}


def test_native_install_help_is_available_without_a_running_service():
    result = CliRunner().invoke(app, ['install', '--help'], color=False)
    assert result.exit_code == 0
    assert '--model' in unstyle(result.stdout)
