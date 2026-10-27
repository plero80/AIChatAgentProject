"""Real startup/factory/cache wiring, with external boundaries replaced locally."""

import asyncio
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
import pytest

import backend.bootstrap as bootstrap
import backend.web.api as api
from backend.config.models import ModelAssignments, ModelConfig, ModelProfile
from backend.config.services import ServiceConfig
from backend.factories.service_factories import ServiceFactories
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.tenancy import load_tenant
from backend.workflows.contracts import ConversationRequest
from backend.workflows.facade import WorkflowFacade


class LocalModel(BaseChatModel):
    label: str

    @property
    def _llm_type(self):
        return "local-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Local"))])


class CountState(TypedDict):
    message: str
    count: int


class CountGraph(BaseChatGraph):
    def __init__(self, *, checkpointer, tenant, **dependencies):
        self.checkpointer = checkpointer
        self.tenant = tenant
        self.started = None
        self.release = None
        builder = StateGraph(CountState)
        builder.add_node("count", lambda state: {"count": state.get("count", 0) + 1})
        builder.add_edge(START, "count")
        builder.add_edge("count", END)
        self.graph = builder.compile(checkpointer=checkpointer)

    async def arun(self, request):
        if self.started is not None:
            self.started.set()
            await self.release.wait()
        if request.message == "fail":
            raise RuntimeError("turn failed")
        state = await self.graph.ainvoke(
            {"message": request.message}, {"configurable": {"thread_id": request.thread_id}},
        )
        return {"final_response": f"Turn {state['count']}"}


@pytest.fixture
def assembly(monkeypatch):
    events, databases, models, graphs, embeddings = [], [], [], [], []
    failures = set()
    factories = ServiceFactories()

    class Database:
        def __init__(self, **kwargs):
            self.closed = 0
            databases.append(self)

        def execute(self, *args):
            pass

        def close(self):
            self.closed += 1
            events.append("database_closed")

    def schema(db):
        if "schema" in failures:
            raise RuntimeError("schema failed")

    def model(*, profile, resources):
        value = LocalModel(label=profile.model)
        models.append(value)
        resources.callback(events.append, "model_closed")
        if profile.model == "broken":
            raise RuntimeError("model failed")
        return value

    def embedding(**kwargs):
        value = SimpleNamespace(**kwargs)
        embeddings.append(value)
        return value

    def graph(*, resources, **dependencies):
        if "graph" in failures:
            raise RuntimeError("graph failed")
        value = CountGraph(**dependencies)
        graphs.append(value)
        resources.callback(events.append, "graph_closed")
        return value

    factories.chat_model.register("local", model, uses_resources=True)
    factories.chat_graph.register("counting", graph, uses_resources=True)
    config = ServiceConfig(chat_graph="counting")
    model_config = ModelConfig(models={"default": ModelProfile(provider="local", model="working")})
    monkeypatch.setattr(bootstrap, "Database", Database)
    monkeypatch.setattr(bootstrap, "apply_schema", schema)
    monkeypatch.setattr(bootstrap, "OpenAIEmbeddings", embedding)
    for module in (bootstrap, api):
        monkeypatch.setattr(module, "ServiceFactories", lambda: factories)
        monkeypatch.setattr(module, "load_service_config", lambda **kwargs: config)
        monkeypatch.setattr(module, "load_model_config", lambda **kwargs: model_config)

    memory = InMemorySaver()
    memory.setup = AsyncMock()

    class CheckpointPool:
        check_connection = None

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            events.append("checkpoint_opened")
            return self

        async def __aexit__(self, *args):
            events.append("checkpoint_closed")

    monkeypatch.setattr(api, "AsyncConnectionPool", CheckpointPool)
    monkeypatch.setattr(api, "AsyncPostgresSaver", lambda pool: memory)
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)
    return SimpleNamespace(
        events=events, databases=databases, models=models, graphs=graphs,
        embeddings=embeddings, memory=memory, failures=failures,
        factories=factories, config=config, model_config=model_config,
    )


def assert_closed(assembly):
    assert all(db.closed == 1 for db in assembly.databases)
    assert assembly.events.count("model_closed") == len(assembly.models)
    assert assembly.events.count("graph_closed") == len(assembly.graphs)
    for embedding in assembly.embeddings:
        assert embedding.http_client.is_closed
        assert embedding.http_async_client.is_closed


def test_default_api_uses_facade_and_closes_runtimes_before_checkpoint_pool(assembly):
    app = api.create_http_app()
    with TestClient(app) as browser:
        facade = app.state.workflow_facade
        assert isinstance(facade, WorkflowFacade)
        assert app.state.workflow.facade is facade
        assert facade.cache.keys == (("clinic_001", "legacy"),)
        assert len(assembly.graphs) == 1  # Warmed at startup.
        assert browser.post("/chat", json={"message": "Hi"}).json()["reply"] == "Turn 1"
        assert browser.post("/chat", json={"message": "Again"}).json()["reply"] == "Turn 2"
        browser.cookies.clear()
        assert browser.post("/chat", json={"message": "Other user"}).json()["reply"] == "Turn 1"
        assert len(assembly.graphs) == len(assembly.models) == len(assembly.databases) == 1
        assert assembly.graphs[0].checkpointer is assembly.memory
        assert assembly.events == ["checkpoint_opened"]
    assert app.state.workflow is app.state.workflow_facade is None
    assert facade.cache.keys == ()
    assert assembly.events == [
        "checkpoint_opened", "graph_closed", "database_closed", "model_closed", "checkpoint_closed",
    ]
    assert_closed(assembly)


@pytest.mark.parametrize("failure", ["schema", "graph"])
def test_startup_failure_closes_partial_resources_and_checkpoint_pool(assembly, failure, offline_admission):
    assembly.failures.add(failure)
    app = api.create_http_app()
    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        with TestClient(app):
            pytest.fail("Startup should fail")
    assert app.state.workflow is app.state.workflow_facade is None
    assert assembly.events[-1] == "checkpoint_closed"
    assert len(assembly.databases) == 1
    assert_closed(assembly)
    assert offline_admission.clients and all(client.is_closed() for client in offline_admission.clients)


def test_partial_model_construction_failure_closes_previous_models(assembly):
    config = ModelConfig(
        models={
            "default": ModelProfile(provider="local", model="working"),
            "second": ModelProfile(provider="local", model="broken"),
        },
        assignments=ModelAssignments(response="second"),
    )

    async def run():
        async with bootstrap.open_chat_workflow(model_config=config):
            pytest.fail("Construction should fail")

    with pytest.raises(RuntimeError, match="model failed"):
        asyncio.run(run())
    assert len(assembly.models) == 2
    assert assembly.databases == []
    assert_closed(assembly)


def test_owned_recipe_factory_eviction_preserves_shared_checkpoints(assembly):
    async def run():
        first, second = load_tenant("clinic_001"), load_tenant("clinic_002")
        a, b = bootstrap.client_for_tenant(first), bootstrap.client_for_tenant(second)
        factories = {
            a.client_id: bootstrap.create_workflow_factory(assembly.memory, tenant=first),
            b.client_id: bootstrap.create_workflow_factory(assembly.memory, tenant=second),
        }
        request = ConversationRequest(message="Hi", client_id=a.client_id, conversation_id="same")
        async with WorkflowFacade(lambda client: factories[client.client_id](client), max_cached_workflows=1) as facade:
            assert (await facade.respond(a, request)).reply == "Turn 1"
            await facade.warm(b)
            assert assembly.databases[0].closed == 1
            assert (await facade.respond(a, request)).reply == "Turn 2"
            assert len(assembly.graphs) == 3
            assert all(graph.checkpointer is assembly.memory for graph in assembly.graphs)
        assert_closed(assembly)

    asyncio.run(run())


def test_factory_rejects_other_client_or_release_before_constructing_resources(assembly):
    async def run():
        factory = bootstrap.create_workflow_factory(assembly.memory)
        client = bootstrap.client_for_tenant(load_tenant("clinic_001"))
        for other in (client.model_copy(update={"release_id": "v2"}), bootstrap.client_for_tenant(load_tenant("clinic_002"))):
            async with WorkflowFacade(factory) as facade:
                with pytest.raises(ValueError, match="configured deployment"):
                    await facade.warm(other)
        assert assembly.models == assembly.databases == []
    asyncio.run(run())
    with pytest.raises(ValueError, match="shared checkpointer"):
        bootstrap.create_workflow_factory(None)


def test_factory_copies_model_selection_at_startup(assembly):
    async def run():
        factory = bootstrap.create_workflow_factory(assembly.memory)
        assembly.model_config.models["default"] = ModelProfile(provider="local", model="changed")
        async with WorkflowFacade(factory) as facade:
            await facade.warm(bootstrap.client_for_tenant(load_tenant("clinic_001")))
            assert assembly.models[0].label == "working"
        assert_closed(assembly)
    asyncio.run(run())


@pytest.mark.parametrize("ending", ["quit", "eof", "interrupt", "error"])
def test_default_cli_closes_resources_on_every_exit(assembly, monkeypatch, capsys, ending):
    messages = iter(["Hi", "Again"])

    def read(prompt):
        try:
            return next(messages)
        except StopIteration:
            if ending == "eof":
                raise EOFError from None
            if ending == "interrupt":
                raise KeyboardInterrupt from None
            return "fail" if ending == "error" else "quit"

    monkeypatch.setattr("builtins.input", read)
    if ending == "error":
        with pytest.raises(RuntimeError, match="turn failed"):
            bootstrap.main(["chat", "--user-id", "terminal"])
    else:
        assert bootstrap.main(["chat", "--user-id", "terminal"]) == (130 if ending == "interrupt" else 0)
    assert "Turn 2" in capsys.readouterr().out
    assert len(assembly.graphs) == len(assembly.databases) == len(assembly.models) == 1
    assert_closed(assembly)


def test_api_shutdown_keeps_checkpoint_pool_open_until_active_turn_finishes(assembly):
    async def run():
        app = api.create_http_app()
        lifespan = api.lifespan(app)
        await lifespan.__aenter__()
        graph = assembly.graphs[0]
        graph.started, graph.release = asyncio.Event(), asyncio.Event()
        request = ConversationRequest(message="Hi", client_id="clinic_001", conversation_id="active")
        turn = asyncio.create_task(app.state.workflow.respond(request))
        await asyncio.wait_for(graph.started.wait(), timeout=5)
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await asyncio.sleep(0)
            assert assembly.events == ["checkpoint_opened"]
            assert not closing.done()
            graph.release.set()
            assert (await asyncio.wait_for(turn, timeout=5)).reply == "Turn 1"
            await asyncio.wait_for(closing, timeout=5)
            assert assembly.events[-1] == "checkpoint_closed"
            assert_closed(assembly)
        finally:
            graph.release.set()
            await asyncio.gather(turn, closing, return_exceptions=True)
    asyncio.run(run())
