"""Installer tests use mocked local models/configuration; no real model calls."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from click import unstyle
from typer.testing import CliRunner

from cli.main import app
from logchat.local import installer


@pytest.fixture
def setup(monkeypatch):
    model = Mock()
    model.installed = AsyncMock(return_value={"chat": True, "embedding": True})
    model.generate = AsyncMock(return_value={"marker": installer.READINESS_MARKER, "ready": True})
    factory = Mock(return_value=model)
    configure = AsyncMock(return_value={"embedding": {
        "provider": "ollama", "model": "nomic-embed-text", "revision": "mock-digest", "dimensions": 768}})
    start = Mock(return_value="http://127.0.0.1:8878")
    monkeypatch.setattr(installer, "LocalModels", factory)
    monkeypatch.setattr(installer, "configure", configure)
    monkeypatch.setattr(installer, "managed_process", Mock(return_value=(None, None)))
    monkeypatch.setattr(installer, "start", start)
    return model, factory, configure, start


def test_noninteractive_setup_uses_one_explicit_generation_model_and_no_start(tmp_path, setup):
    model, factory, configure, start = setup
    result = CliRunner().invoke(app, ["install", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--state-dir", str(tmp_path),
                                    "--endpoint", "http://127.0.0.1:11434/", "--model", "chosen:7b"])
    assert result.exit_code == 0, result.output
    factory.assert_called_once_with(base_url="http://127.0.0.1:11434", chat_model="chosen:7b",
                                    embedding_model="nomic-embed-text", preserve_content=True)
    configure.assert_awaited_once_with(tmp_path, base_url="http://127.0.0.1:11434", model="nomic-embed-text",
                                      dimensions=768, chat_model="chosen:7b", content_policy="local_model_compact")
    assert model.generate.await_count == 1
    assert model.generate.call_args.args[1] == {"marker": installer.READINESS_MARKER, "ready": True}
    start.assert_not_called()
    assert '"shared_model": "chosen:7b"' in result.output
    assert "does not install Python dependencies or download models" in result.output
    assert "Queries return cited context to your agent" in result.output
    assert '"embedding"' not in result.output and '"readiness"' not in result.output


def test_interactive_wizard_only_prompts_for_endpoint_one_model_and_optional_start(tmp_path, setup):
    _, factory, configure, start = setup
    result = CliRunner().invoke(app, ["install", "--state-dir", str(tmp_path)],
                               input="http://localhost:11434\nselected-model:latest\nnomic-embed-text\n768\nn\n")
    assert result.exit_code == 0, result.output
    assert "Local Ollama endpoint" in result.output
    assert "Shared generation model" in result.output
    assert "Start this Logchat instance now?" in result.output
    assert "Embedding model" in result.output
    assert factory.call_args.kwargs["chat_model"] == "selected-model:latest"
    assert configure.call_args.kwargs["chat_model"] == "selected-model:latest"
    start.assert_not_called()


def test_fresh_noninteractive_install_requires_model_selection(tmp_path, setup):
    _, factory, configure, start = setup
    result = CliRunner().invoke(app, ["install", "--yes", "--no-start", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "no model is selected by default" in result.output
    factory.assert_not_called()
    configure.assert_not_awaited()
    start.assert_not_called()


def test_fresh_install_requires_explicit_embedding_profile(tmp_path, setup):
    _, factory, configure, _ = setup
    result = CliRunner().invoke(app, ["install", "--yes", "--state-dir", str(tmp_path), "--model", "chosen:7b"])
    assert result.exit_code == 1
    assert "--embedding-model and --dimensions" in result.output
    factory.assert_not_called()
    configure.assert_not_awaited()


def test_existing_profile_reuses_selected_model_and_embedding_spec(tmp_path, setup):
    _, factory, configure, _ = setup
    previous = {"base_url": "http://localhost:11435", "generation_model": "existing:9b",
                "embedding": {"model": "existing-embed", "dimensions": 384}}
    (tmp_path / "rag.json").write_text(json.dumps(previous))
    result = CliRunner().invoke(app, ["install", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert factory.call_args.kwargs["chat_model"] == "existing:9b"
    assert configure.call_args.kwargs["model"] == "existing-embed"
    assert configure.call_args.kwargs["dimensions"] == 384


def test_conflicting_legacy_stage_override_is_not_silently_erased(tmp_path, setup):
    _, factory, configure, _ = setup
    previous = {"base_url": installer.DEFAULT_ENDPOINT, "generation_model": "shared:7b", "chunk_model": "other:7b",
                "embedding": {"model": "nomic-embed-text", "dimensions": 768}}
    original = json.dumps(previous)
    (tmp_path / "rag.json").write_text(original)
    result = CliRunner().invoke(app, ["install", "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Stage model overrides are unsupported" in result.output
    assert (tmp_path / "rag.json").read_text() == original
    factory.assert_not_called()
    configure.assert_not_awaited()


def test_detailed_readiness_remains_internal(tmp_path, setup):
    report = asyncio.run(installer.prepare_installation(tmp_path, endpoint=installer.DEFAULT_ENDPOINT, model="chosen:7b", embedding_model="nomic-embed-text", dimensions=768))
    assert report["readiness"]["structured_output"] is True
    assert report["readiness"]["probe"] == "synthetic_marker_only"
    assert report["generation_roles"] == ["chunking", "relevance", "summary"]


@pytest.mark.parametrize("endpoint", ["https://models.example.test", "http://ollama:11434",
    "http://127.0.0.1:11434/v1", "http://127.0.0.1:99999", "http://user:PRIVATE_SECRET@localhost:11434",
    "http://localhost:11434?api_key=PRIVATE_SECRET"])
def test_unsupported_or_credential_endpoint_fails_before_model_access(tmp_path, setup, endpoint):
    _, factory, configure, start = setup
    result = CliRunner().invoke(app, ["install", "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--state-dir", str(tmp_path), "--endpoint", endpoint])
    assert result.exit_code == 1
    assert "loopback Ollama endpoint" in result.output
    assert "PRIVATE_SECRET" not in result.output
    factory.assert_not_called()
    configure.assert_not_awaited()
    start.assert_not_called()


@pytest.mark.parametrize("missing", ["chat", "embedding"])
def test_missing_installed_capability_does_not_write_config_or_choose_another(tmp_path, setup, missing):
    model, factory, configure, start = setup
    model.installed.return_value = {"chat": True, "embedding": True, missing: False}
    result = CliRunner().invoke(app, ["install", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--start", "--state-dir", str(tmp_path), "--model", "chosen:7b"])
    assert result.exit_code == 1
    assert "not available" in result.output
    factory.assert_called_once()
    assert factory.call_args.kwargs["chat_model"] == "chosen:7b"
    model.generate.assert_not_awaited()
    configure.assert_not_awaited()
    start.assert_not_called()
    assert not (tmp_path / "rag.json").exists()


@pytest.mark.parametrize("reply", [{"marker": "wrong", "ready": True},
    {"marker": installer.READINESS_MARKER, "ready": False},
    {"marker": installer.READINESS_MARKER, "ready": 1},
    {"marker": installer.READINESS_MARKER, "ready": True, "extra": "ignored schema"},
    "not structured output"])
def test_invalid_structured_reply_prevents_configuration(tmp_path, setup, reply):
    model, _, configure, start = setup
    model.generate.return_value = reply
    result = CliRunner().invoke(app, ["install", "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--start", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "structured-output readiness" in result.output
    configure.assert_not_awaited()
    start.assert_not_called()


@pytest.mark.parametrize("stage", ["installed", "generate", "configure"])
def test_errors_and_timeouts_do_not_expose_provider_payloads_or_start(tmp_path, setup, stage):
    model, _, configure, start = setup
    target = configure if stage == "configure" else getattr(model, stage)
    target.side_effect = TimeoutError("PRIVATE_PROVIDER_PAYLOAD")
    result = CliRunner().invoke(app, ["install", "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--start", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "PRIVATE_PROVIDER_PAYLOAD" not in result.output
    start.assert_not_called()
    if stage != "configure":
        configure.assert_not_awaited()


def test_existing_instance_must_stop_before_model_or_configuration_calls(tmp_path, setup, monkeypatch):
    _, factory, configure, start = setup
    monkeypatch.setattr(installer, "managed_process", lambda directory: (object(), {}))
    result = CliRunner().invoke(app, ["install", "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Stop the instance" in result.output
    factory.assert_not_called()
    configure.assert_not_awaited()
    start.assert_not_called()


def test_optional_start_occurs_after_model_probe_and_configuration(tmp_path, setup):
    model, _, configure, start = setup
    order = []
    async def generate(*args):
        order.append("structured_probe")
        return {"marker": installer.READINESS_MARKER, "ready": True}
    async def save(*args, **kwargs):
        order.append("configure")
        return {"embedding": {"model": "nomic-embed-text", "dimensions": 768}}
    model.generate.side_effect = generate
    configure.side_effect = save
    start.side_effect = lambda **kwargs: order.append("start") or "http://127.0.0.1:8878"
    result = CliRunner().invoke(app, ["install", "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--start", "--port", "8878", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert order == ["structured_probe", "configure", "start"]
    start.assert_called_once_with(port=8878, state_dir=tmp_path)
    assert '"status": "running"' in result.output
    assert "--logchat-port 8878" in result.output
    assert str(tmp_path) in result.output


def test_start_failure_reports_saved_configuration_not_install_success(tmp_path, setup):
    _, _, configure, start = setup
    start.side_effect = RuntimeError("PRIVATE_START_ERROR")
    result = CliRunner().invoke(app, ["install", "--model", "chosen:7b", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--start", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1
    configure.assert_awaited_once()
    assert "configuration was saved" in result.output
    assert "PRIVATE_START_ERROR" not in result.output


def test_configuration_rejection_preserves_existing_file(tmp_path, setup):
    _, _, configure, _ = setup
    original = json.dumps({"base_url": installer.DEFAULT_ENDPOINT, "generation_model": "original:7b",
                           "embedding": {"model": "nomic-embed-text", "dimensions": 768}})
    (tmp_path / "rag.json").write_text(original)
    configure.side_effect = ValueError("embedding_index_model_mismatch")
    result = CliRunner().invoke(app, ["install", "--embedding-model", "nomic-embed-text", "--dimensions", "768",  "--yes", "--state-dir", str(tmp_path), "--model", "new:7b"])
    assert result.exit_code == 1
    assert "existing index" in result.output
    assert (tmp_path / "rag.json").read_text() == original


def test_help_exposes_one_generation_model_and_no_stage_overrides(setup):
    result = CliRunner().invoke(app, ["install", "--help"])
    assert result.exit_code == 0
    assert "--model" in unstyle(result.output)
    assert "--yes" in unstyle(result.output)
    assert "--chunk-model" not in unstyle(result.output)
    assert "--relevance-model" not in unstyle(result.output)
    assert "--embedding-model" in unstyle(result.output)
