import asyncio
import json
import os
from unittest.mock import MagicMock

import pytest

import backend.bootstrap as main
from backend.bootstrap import create_chat_workflow
from backend.cli.commands.base_command import BaseCommand
from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import RecipeIngestionRequest
from backend.clients.context import ClientContext
from backend.models.chat_response import ChatResponse
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.cli.runner import run_cli
from backend.config.models import MODEL_ENV_VARS
from backend.config.services import SERVICE_ENV_VARS


@pytest.fixture(autouse=True)
def no_external_dependencies(monkeypatch):
    for name in ("MODELS_CONFIG", "SERVICES_CONFIG", *MODEL_ENV_VARS.values(), *SERVICE_ENV_VARS.values()):
        # Record even absent keys so CLI exports are undone after this test.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    chat = MagicMock(side_effect=AssertionError("Unexpected chat construction"))
    ingest = MagicMock(side_effect=AssertionError("Unexpected ingestion construction"))
    monkeypatch.setattr(main, "open_chat_workflow", chat)
    monkeypatch.setattr(main, "create_ingest_recipe", ingest)
    return chat, ingest


class RecordingGraph(BaseGraph):
    def __init__(self, result):
        self.result = result
        self.requests = []
        self.loops = []

    async def arun(self, request):
        self.requests.append(request)
        self.loops.append(asyncio.get_running_loop())
        return self.result


class RecordingWorkflow(ChatWorkflow):
    def __init__(self, *, client=None):
        super().__init__(client or ClientContext(client_id="clinic_001", display_name="Alona"))
        self.requests = []
        self.loops = []

    async def _respond(self, request):
        self.requests.append(request)
        self.loops.append(asyncio.get_running_loop())
        return ChatResponse(reply="Reply")


def test_chat_constructs_one_workflow_and_keeps_one_session_and_event_loop(monkeypatch, capsys):
    graph = RecordingWorkflow()
    factory = MagicMock(return_value=graph)
    monkeypatch.setattr(main, "open_chat_workflow", factory)
    messages = iter(["  Hi!  ", "", "Hello!", "QUIT"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(messages))

    assert main.main(["chat", "--user-id", "session-a"]) == 0
    factory.assert_called_once_with()
    assert graph.requests == [
        ConversationRequest(message=message, conversation_id="session-a", client_id="clinic_001")
        for message in ("Hi!", "Hello!")
    ]
    assert graph.loops[0] is graph.loops[1]
    assert capsys.readouterr().out.count("Alona: Reply") == 2


def test_chat_generates_a_stable_session_id_and_handles_end_of_input(monkeypatch):
    graph = RecordingWorkflow()
    monkeypatch.setattr(main, "open_chat_workflow", lambda: graph)
    messages = iter(["Hi", "Again"])

    def read(prompt):
        try:
            return next(messages)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", read)
    assert main.main(["chat"]) == 0
    assert graph.requests[0].conversation_id
    assert graph.requests[0].conversation_id == graph.requests[1].conversation_id


def test_chat_keyboard_interrupt_returns_130(monkeypatch):
    monkeypatch.setattr(main, "open_chat_workflow", RecordingWorkflow)
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=KeyboardInterrupt))
    assert main.main(["chat"]) == 130


def test_ingest_reads_utf8_file_and_uses_its_own_request_type(tmp_path, monkeypatch, capsys):
    path = tmp_path / "recipe with spaces.txt"
    path.write_text("מתכון\nIngredients and instructions", encoding="utf-8-sig")
    graph = RecordingGraph({"success": True, "recipe_id": "recipe-123"})
    factory = MagicMock(return_value=graph)
    monkeypatch.setattr(main, "create_ingest_recipe", factory)

    assert main.main(["ingest", "--file", str(path)]) == 0
    factory.assert_called_once_with()
    assert graph.requests == [RecipeIngestionRequest("מתכון\nIngredients and instructions", dietitian_id="clinic_001")]
    assert "Recipe saved: recipe-123" in capsys.readouterr().out


@pytest.mark.parametrize("content", [None, b"   \n", b"\xff\xfe"])
def test_bad_recipe_file_does_not_construct_a_graph(tmp_path, content, capsys, no_external_dependencies):
    path = tmp_path / "recipe.txt"
    if content is not None:
        path.write_bytes(content)
    assert main.main(["ingest", "--file", str(path)]) == 1
    assert capsys.readouterr().err
    for factory in no_external_dependencies:
        factory.assert_not_called()


def test_ingestion_failure_is_reported_with_nonzero_exit(tmp_path, monkeypatch, capsys):
    path = tmp_path / "recipe.txt"
    path.write_text("Recipe", encoding="utf-8")
    graph = RecordingGraph({"success": False, "error": "Recipe already exists"})
    monkeypatch.setattr(main, "create_ingest_recipe", lambda: graph)
    assert main.main(["ingest", "--file", str(path)]) == 1
    assert "Recipe already exists" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["unknown"], ["ingest"], ["chat", "--typo"], ["--service", "chat_model=openai"]])
def test_invalid_commands_fail_before_construction(argv, no_external_dependencies):
    with pytest.raises(SystemExit) as exc:
        main.main(argv)
    assert exc.value.code == 2
    for factory in no_external_dependencies:
        factory.assert_not_called()


@pytest.mark.parametrize("argv", [["--help"], ["chat", "--help"], ["ingest", "--help"]])
def test_help_does_not_construct_graphs(argv, capsys, no_external_dependencies):
    with pytest.raises(SystemExit) as exc:
        main.main(argv)
    assert exc.value.code == 0
    assert "--models-config" in capsys.readouterr().out
    for factory in no_external_dependencies:
        factory.assert_not_called()


def test_no_task_prints_help_and_does_not_default_to_ingestion(capsys, no_external_dependencies):
    assert main.main([]) == 0
    output = capsys.readouterr().out
    assert "chat" in output and "ingest" in output
    for factory in no_external_dependencies:
        factory.assert_not_called()


def test_inspection_still_works_without_a_task(capsys, no_external_dependencies):
    assert main.main(["--show-models"]) == 0
    assert "model_config" in json.loads(capsys.readouterr().out)
    for factory in no_external_dependencies:
        factory.assert_not_called()


def test_shared_flags_before_and_after_task_are_combined(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": {
        "first": {"provider": "openai", "model": "first-model"},
        "second": {"provider": "openai", "model": "second-model"},
    }}), encoding="utf-8")
    graph = RecordingWorkflow()

    def create():
        assert os.environ["CHAT_MODEL_PROFILE"] == "second"
        assert os.environ["RESPONSE_MODEL_PROFILE"] == "first"
        assert os.environ["MODELS_CONFIG"] == str(path.resolve())
        return graph

    monkeypatch.setattr(main, "open_chat_workflow", create)
    monkeypatch.setattr("builtins.input", lambda prompt: "quit")
    assert main.main([
        "--models-config", str(path), "--model", "chat=first",
        "--model", "response=first", "chat", "--model", "chat=second",
    ]) == 0


def test_invalid_configuration_does_not_construct_a_graph(no_external_dependencies):
    with pytest.raises(SystemExit) as exc:
        main.main(["chat", "--model", "chat=missing"])
    assert exc.value.code == 2
    assert "CHAT_MODEL_PROFILE" not in os.environ
    for factory in no_external_dependencies:
        factory.assert_not_called()


def test_another_graph_and_command_can_be_registered_without_changing_dispatch(capsys):
    class UppercaseGraph(BaseGraph[str, str]):
        async def arun(self, request: str) -> str:
            return request.upper()

    class UppercaseCommand(BaseCommand):
        description = "Uppercase supplied text."

        def add_arguments(self, parser):
            parser.add_argument("--text", required=True)

        async def run(self, args):
            print(await UppercaseGraph().arun(args.text))
            return 0

    assert run_cli(["uppercase", "--text", "hello"], commands={"uppercase": UppercaseCommand()}) == 0
    assert capsys.readouterr().out.strip() == "HELLO"


def test_cli_chat_and_ingestion_use_the_selected_deployment(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DIETITIAN_ID", "clinic_002")
    chat = RecordingGraph({"final_response": "Reply"})
    # Exercise the actual tenant-to-client mapping and recipe adapter for CLI.
    monkeypatch.setattr(main, "open_chat_workflow", lambda: create_chat_workflow(
        graph_factory=lambda **dependencies: chat,
    ))
    messages = iter(["Hi", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(messages))
    assert main.main(["chat"]) == 0
    assert chat.requests[0].dietitian_id == "clinic_002"
    assert "Dana: Reply" in capsys.readouterr().out
    path = tmp_path / "recipe.txt"
    path.write_text("Dana recipe", encoding="utf-8")
    ingestion = RecordingGraph({"success": True, "recipe_id": "test"})
    monkeypatch.setattr(main, "create_ingest_recipe", lambda: ingestion)
    assert main.main(["ingest", "--file", str(path)]) == 0
    assert ingestion.requests[0].dietitian_id == "clinic_002"
