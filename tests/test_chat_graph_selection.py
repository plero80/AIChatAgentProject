import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

import backend.web.api as api
import backend.bootstrap as main
import backend.cli.options as ServiceCLI
from backend.factories.service_factories import ServiceFactories
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.graph_state import GraphState
from backend.graphs.recipe_ingestion_graph import RecipeIngestionGraph
from backend.config.models import MODEL_ENV_VARS
from backend.config.services import SERVICE_ENV_VARS, ServiceConfigurationError, load_service_config


class ExperimentalChatGraph(BaseChatGraph):
    """An independent test workflow, not a subclass of RecipeChatGraph."""

    def __init__(self, *, nodes, checkpointer=None, model_registry=None):
        self.nodes = nodes
        self.model_registry = model_registry
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.requests = []
        workflow = StateGraph(GraphState)
        workflow.add_node("draft", nodes.chat)
        workflow.add_node("decorate", self.decorate)
        workflow.add_edge(START, "draft")
        workflow.add_edge("draft", "decorate")
        workflow.add_edge("decorate", END)
        self.workflow = workflow.compile(checkpointer=self.checkpointer)

    async def decorate(self, state):
        reply = state["result"]["text"] + " [experimental]"
        return {
            "final_response": reply,
            "response_blocks": [{"type": "text", "text": reply}],
            "messages": [AIMessage(content=reply)],
        }

    async def arun(self, request: ChatGraphRequest) -> GraphState:
        self.requests.append(request)
        return await self.workflow.ainvoke({
            "message": request.message,
            "user_id": request.user_id,
            "final_response": None,
            "response_blocks": None,
            "messages": [HumanMessage(content=request.message)],
        }, config={"configurable": {"thread_id": request.user_id}})


@pytest.fixture
def experiment(monkeypatch):
    for name in ("SERVICES_CONFIG", "MODELS_CONFIG", *SERVICE_ENV_VARS.values(), *MODEL_ENV_VARS.values()):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api, "_graph", None)
    factories = ServiceFactories()
    created = []

    def build(**dependencies):
        graph = ExperimentalChatGraph(**dependencies)
        created.append(graph)
        return graph

    factories.chat_graph.register("experimental", build)
    monkeypatch.setattr(api, "ServiceFactories", lambda: factories)
    monkeypatch.setattr(main, "ServiceFactories", lambda: factories)
    monkeypatch.setattr(ServiceCLI, "ServiceFactories", lambda: factories)

    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content="Alternate answer"))
    models = SimpleNamespace(for_role=lambda role: model)
    dependencies = MagicMock(return_value=(models, object(), object(), object(), object()))
    monkeypatch.setattr(main, "get_required_services", dependencies)
    return SimpleNamespace(factories=factories, created=created, models=models, dependencies=dependencies)


def test_chat_factory_rejects_ingestion_graphs_and_wrong_builder_results():
    factory = ServiceFactories().chat_graph
    with pytest.raises(TypeError, match="must implement BaseChatGraph"):
        factory.register("ingestion", RecipeIngestionGraph)
    factory.register("broken", lambda **kwargs: object())
    with pytest.raises(TypeError, match="must return BaseChatGraph"):
        factory.create("broken")


def test_chat_graph_choice_follows_existing_configuration_precedence(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"chat_graph": "from_file"}), encoding="utf-8")
    assert load_service_config(path, environ={}).chat_graph == "from_file"
    assert load_service_config(path, environ={"CHAT_GRAPH_TYPE": "from_env"}).chat_graph == "from_env"
    assert load_service_config(path, environ={"CHAT_GRAPH_TYPE": "from_env"}, overrides={"chat_graph": "from_cli"}).chat_graph == "from_cli"


def test_api_runs_selected_chat_graph_once_with_existing_checkpointing(experiment, monkeypatch):
    monkeypatch.setenv("CHAT_GRAPH_TYPE", "experimental")
    memory = InMemorySaver()
    memory.setup = AsyncMock()
    pool = MagicMock()
    pool.__aenter__ = AsyncMock(return_value=pool)
    pool.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(api, "AsyncConnectionPool", MagicMock(return_value=pool))
    saver_factory = MagicMock(return_value=memory)
    monkeypatch.setattr(api, "AsyncPostgresSaver", saver_factory)
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)

    with TestClient(api.app) as client_a:
        graph = api.get_graph()
        assert isinstance(graph, BaseChatGraph)
        assert isinstance(graph, ExperimentalChatGraph)
        assert graph.checkpointer is memory
        assert graph.model_registry is experiment.models
        for message in ("Hi!", "Again"):
            response = client_a.post("/chat", json={"message": message})
            assert response.status_code == 200
            assert response.json() == {
                "reply": "Alternate answer [experimental]",
                "blocks": [{"type": "text", "text": "Alternate answer [experimental]"}],
            }
        # A second HTTP session shares the graph but has a different history ID.
        client_b = TestClient(api.app)
        try:
            assert client_b.post("/chat", json={"message": "Hello!"}).status_code == 200
        finally:
            client_b.close()
        a1, a2, b = graph.requests
        assert a1.user_id == a2.user_id
        assert a1.user_id != b.user_id
        state_a = graph.workflow.get_state({"configurable": {"thread_id": a1.user_id}}).values
        state_b = graph.workflow.get_state({"configurable": {"thread_id": b.user_id}}).values
        assert [m.content for m in state_a["messages"] if isinstance(m, HumanMessage)] == ["Hi!", "Again"]
        assert [m.content for m in state_b["messages"] if isinstance(m, HumanMessage)] == ["Hello!"]
        assert api.get_graph() is graph
    assert len(experiment.created) == 1
    experiment.dependencies.assert_called_once()
    saver_factory.assert_called_once_with(pool)
    memory.setup.assert_awaited_once()


@pytest.mark.parametrize("entry", ["api", "cli_factory"])
def test_unknown_chat_graph_fails_before_external_dependencies(experiment, monkeypatch, entry):
    monkeypatch.setenv("CHAT_GRAPH_TYPE", "missing")
    pool = MagicMock(side_effect=AssertionError("Checkpoint pool opened"))
    monkeypatch.setattr(api, "AsyncConnectionPool", pool)
    with pytest.raises(ServiceConfigurationError, match="Unknown chat_graph.*Available: experimental, recipe"):
        if entry == "api":
            with TestClient(api.app):
                pytest.fail("Invalid graph selection started the API")
        else:
            main.create_app()
    pool.assert_not_called()
    experiment.dependencies.assert_not_called()
    assert experiment.created == []


def test_cli_and_api_launcher_select_the_same_chat_graph(experiment, monkeypatch, capsys):
    # The API launcher uses this same configuration function before uvicorn starts.
    assert ServiceCLI.configure_service_cli(["--service", "chat_graph=experimental"])
    assert os.environ["CHAT_GRAPH_TYPE"] == "experimental"
    messages = iter(["Hi!", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(messages))
    assert main.main(["chat"]) == 0
    assert "Alternate answer [experimental]" in capsys.readouterr().out
    assert len(experiment.created) == 1


def test_inspection_lists_graph_choices_without_building_them(experiment, capsys):
    assert not ServiceCLI.configure_service_cli([
        "--service", "chat_graph=experimental", "--show-services",
    ])
    choices = json.loads(capsys.readouterr().out)
    assert choices["selected"]["chat_graph"] == "experimental"
    assert choices["available"]["chat_graph"] == ["experimental", "recipe"]
    experiment.dependencies.assert_not_called()
    assert experiment.created == []
