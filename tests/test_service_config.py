import json
import os

import pytest

from backend.factories.service_factories import ServiceFactories
from backend.config.models import MODEL_ENV_VARS
from backend.cli.options import configure_service_cli
from backend.config.services import (
    SERVICE_ENV_VARS,
    ServiceConfig,
    ServiceConfigurationError,
    load_service_config,
)


def config_file(tmp_path, values):
    path = tmp_path / "services.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clean_service_environment(monkeypatch):
    for variable in ["SERVICES_CONFIG", "MODELS_CONFIG", *SERVICE_ENV_VARS.values(), *MODEL_ENV_VARS.values()]:
        monkeypatch.delenv(variable, raising=False)


def test_config_precedence_and_defaults(tmp_path):
    path = config_file(tmp_path, {"message_analyzer": "from_file", "dietitian": "file"})
    env = {"SERVICES_CONFIG": str(path), "MESSAGE_ANALYZER_TYPE": "from_env"}
    assert load_service_config(environ=env).message_analyzer == "from_env"
    config = load_service_config(environ=env, overrides={"message_analyzer": "from_cli"})
    assert config.message_analyzer == "from_cli"
    assert config.recipe_search == "postgres"
    assert load_service_config(path, environ={}).message_analyzer == "from_file"


def test_default_file_works_outside_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_service_config(environ={})
    ServiceFactories().validate(config)
    assert config == ServiceConfig()


@pytest.mark.parametrize("values", [
    [], {"message_analzyer": "llm"}, {"message_analyzer": 123},
    {"message_analyzer": None}, {"message_analyzer": ""},
    {"message_analyzer": "Services.CustomAnalyzer"},
])
def test_invalid_configuration_is_rejected(tmp_path, values):
    with pytest.raises(ServiceConfigurationError):
        load_service_config(config_file(tmp_path, values), environ={})


def test_missing_and_malformed_files_are_not_silently_ignored(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(ServiceConfigurationError, match="Cannot read service config"):
        load_service_config(environ={"SERVICES_CONFIG": str(path)})
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ServiceConfigurationError, match="Cannot read service config"):
        load_service_config(path, environ={})


def test_unknown_implementation_lists_valid_choices():
    with pytest.raises(ServiceConfigurationError, match="Unknown message_analyzer.*Available: llm"):
        ServiceFactories().validate(ServiceConfig(message_analyzer="typo"))


def test_cli_overrides_survive_worker_imports(tmp_path, monkeypatch):
    path = config_file(tmp_path, {"message_analyzer": "from_file"})
    monkeypatch.setenv("SERVICES_CONFIG", str(tmp_path / "unused.json"))
    monkeypatch.setenv("MESSAGE_ANALYZER_TYPE", "from_env")
    assert configure_service_cli([
        "--services-config", str(path), "--service", "message_analyzer=llm",
    ])
    # A freshly imported API process reads these environment values, not argparse state.
    assert os.environ["SERVICES_CONFIG"] == str(path.resolve())
    assert load_service_config().message_analyzer == "llm"


def test_show_services_only_inspects_configuration(capsys):
    assert not configure_service_cli(["--show-services"])
    output = json.loads(capsys.readouterr().out)
    assert output["selected"]["message_analyzer"] == "llm"
    assert output["available"]["message_analyzer"] == ["llm"]


@pytest.mark.parametrize("value", ["message_analyzer=typo", "typo=llm", "message_analyzer"])
def test_cli_rejects_bad_choices_without_exporting_them(value):
    before = dict(os.environ)
    with pytest.raises(SystemExit) as error:
        configure_service_cli(["--service", value])
    assert error.value.code == 2
    assert dict(os.environ) == before
