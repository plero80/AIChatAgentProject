import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.config.models as model_config_module
from backend.factories.model_registry import ModelRegistry
from backend.config.models import MODEL_ENV_VARS, ModelAssignments, load_model_config
from backend.cli.options import configure_service_cli
from backend.config.services import SERVICE_ENV_VARS, ServiceConfig, ServiceConfigurationError


@pytest.fixture(autouse=True)
def clean_selection_environment(monkeypatch):
    for name in ("MODELS_CONFIG", "SERVICES_CONFIG", *MODEL_ENV_VARS.values(), *SERVICE_ENV_VARS.values()):
        monkeypatch.delenv(name, raising=False)


def model_file(tmp_path, content):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_implicit_default_preserves_existing_settings_outside_cwd(tmp_path, monkeypatch):
    # Exercise the default-path lookup without reading the developer's models.json.
    path = model_file(tmp_path, {})
    monkeypatch.setattr(model_config_module, "DEFAULT_MODELS_CONFIG", path)
    working_directory = tmp_path / "elsewhere"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    settings = SimpleNamespace(CHAT_MODEL="legacy-model", CHAT_MAX_TOKENS=1234, OPENAI_REASONING_EFFORT="none")
    config = load_model_config(service_config=ServiceConfig(), settings=settings, environ={})
    assert config.models["default"].model_dump() == {
        "provider": "openai", "model": "legacy-model", "max_tokens": 1234, "reasoning_effort": "none",
    }
    assert set(config.assignments.model_dump().values()) == {"default"}
    alternate = load_model_config(service_config=ServiceConfig(chat_model="anthropic"), settings=settings, environ={})
    assert alternate.models["default"].provider == "anthropic"
    assert alternate.models["default"].reasoning_effort is None


def test_explicit_profiles_are_independent_and_assignments_follow_precedence(tmp_path):
    path = model_file(tmp_path, {
        "models": {
            "default": {"provider": "anthropic", "model": "explicit-default", "max_tokens": 3000},
            "fast": {"provider": "google_genai", "model": "fast-model"},
            "writer": {"provider": "openai", "model": "writing-model", "max_tokens": 9000},
        },
        "assignments": {"chat": "writer", "message_analyzer": "fast"},
    })
    env = {"MODELS_CONFIG": str(path), "CHAT_MODEL_PROFILE": "fast"}
    config = load_model_config(service_config=ServiceConfig(), environ=env)
    assert config.assignments.chat == "fast"
    assert config.assignments.message_analyzer == "fast"
    assert config.assignments.response == "default"
    assert config.models["default"].provider == "anthropic"
    assert config.models["default"].reasoning_effort is None
    assert config.models["writer"].max_tokens == 9000
    assert load_model_config(service_config=ServiceConfig(), environ=env, overrides={"chat": "writer"}).assignments.chat == "writer"


@pytest.mark.parametrize("content", [
    [], {"models": None}, {"assignments": []}, {"modles": {}},
    {"models": {"Bad Name": {"provider": "openai", "model": "x"}}},
    {"models": {"x": {"provider": "openai"}}},
    {"models": {"x": {"provider": "openai", "model": "  "}}},
    {"models": {"x": {"provider": "openai", "model": 123}}},
    {"models": {"x": {"provider": "openai", "model": "x", "max_tokens": 0}}},
    {"models": {"x": {"provider": "openai", "model": "x", "max_tokens": True}}},
    {"models": {"x": {"provider": "openai", "model": "x", "api_key": "keys-belong-in-env"}}},
    {"models": {"x": {"provider": "anthropic", "model": "x", "reasoning_effort": "none"}}},
    {"assignments": {"chat": "missing"}}, {"assignments": {"chta": "default"}},
])
def test_invalid_profiles_or_assignments_are_rejected(tmp_path, content):
    with pytest.raises(ServiceConfigurationError):
        load_model_config(model_file(tmp_path, content), service_config=ServiceConfig(), environ={})


def test_missing_or_malformed_model_file_is_an_error(tmp_path):
    path = tmp_path / "missing.json"
    for contents in (None, "{oops"):
        if contents is not None:
            path.write_text(contents, encoding="utf-8")
        with pytest.raises(ServiceConfigurationError, match="Cannot read model config"):
            load_model_config(path, service_config=ServiceConfig(), environ={})


def test_model_cli_overrides_are_exported_only_after_validation(tmp_path, monkeypatch):
    path = model_file(tmp_path, {"models": {"writer": {"provider": "anthropic", "model": "writer-model"}}})
    monkeypatch.setenv("MODELS_CONFIG", str(tmp_path / "ignored.json"))
    monkeypatch.setenv("CHAT_MODEL_PROFILE", "missing")
    assert configure_service_cli(["--models-config", str(path), "--model", "chat=writer"])
    assert os.environ["MODELS_CONFIG"] == str(path.resolve())
    assert load_model_config().assignments.chat == "writer"
    assert load_model_config().assignments.response == "default"


@pytest.mark.parametrize("value", ["chat=missing", "unknown=default", "chat"])
def test_invalid_model_cli_does_not_export_partial_changes(value):
    before = dict(os.environ)
    with pytest.raises(SystemExit) as exc:
        configure_service_cli(["--service", "message_analyzer=llm", "--model", value])
    assert exc.value.code == 2
    assert dict(os.environ) == before


def test_show_models_resolves_profiles_without_building_clients(tmp_path, monkeypatch, capsys):
    path = model_file(tmp_path, {"models": {"writer": {"provider": "anthropic", "model": "writer-model"}}})
    build = MagicMock(side_effect=AssertionError("Client constructed during inspection"))
    monkeypatch.setattr(ModelRegistry, "__init__", build)
    assert not configure_service_cli(["--models-config", str(path), "--model", "response=writer", "--show-models"])
    output = json.loads(capsys.readouterr().out)
    assert output["model_config"]["assignments"]["response"] == "writer"
    assert output["model_config"]["models"]["writer"]["provider"] == "anthropic"
    assert set(output["model_config"]["assignments"]) == set(ModelAssignments.model_fields)
    build.assert_not_called()
    assert "MODELS_CONFIG" not in os.environ
