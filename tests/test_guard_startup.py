"""Default admission assembly: real entry points, fake provider/DB boundaries."""

import asyncio
import json
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from psycopg import OperationalError
import pytest
from typing_extensions import Annotated

import backend.bootstrap as bootstrap
import backend.web.api as api
from backend.config.guard import GuardConfig, load_guard_config
from backend.config.services import ServiceConfigurationError
from backend.guards.history import CheckpointHistoryLoader
from backend.tenancy import load_tenant
from backend.workflows.contracts import ConversationRequest
from backend.workflows.guarded import GuardedWorkflow


class State(TypedDict):
    message: str
    messages: Annotated[list, add_messages]
    final_response: str


class RecordingMemory(InMemorySaver):
    def __init__(self):
        super().__init__()
        self.operations = []

    async def aput(self, *args, **kwargs):
        self.operations.append("checkpoint")
        return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        self.operations.append("writes")
        return await super().aput_writes(*args, **kwargs)


@pytest.fixture
def application(monkeypatch):
    memory = RecordingMemory()
    memory.setup = AsyncMock()
    pool = MagicMock()
    pool.__aenter__ = AsyncMock(return_value=pool)
    pool.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(api, "AsyncConnectionPool", MagicMock(return_value=pool))
    monkeypatch.setattr(api, "AsyncPostgresSaver", lambda _: memory)
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)
    calls, graphs, closed = [], [], []
    settings = SimpleNamespace(fail_graph=False)

    class Graph:
        def __init__(self, checkpointer):
            builder = StateGraph(State)
            builder.add_node("reply", lambda state: {
                "final_response": "Approved reply", "messages": [AIMessage(content="Approved reply")],
            })
            builder.add_edge(START, "reply")
            builder.add_edge("reply", END)
            self.graph = builder.compile(checkpointer=checkpointer)

        async def arun(self, request):
            calls.append(request)
            if settings.fail_graph:
                raise OperationalError("temporary database failure")
            return await self.graph.ainvoke(
                {"message": request.message, "messages": [HumanMessage(content=request.message)]},
                {"configurable": {"thread_id": request.thread_id}},
            )

    def graph_factory(*, checkpointer, resources, **kwargs):
        graph = Graph(checkpointer)
        graphs.append(graph)
        resources.callback(closed.append, graph)
        return graph

    return SimpleNamespace(
        app=api.create_http_app(graph_factory=graph_factory), memory=memory, calls=calls,
        graphs=graphs, closed=closed, settings=settings, graph_factory=graph_factory,
    )


def answer(unsafe=0.0, relevant=1.0):
    return httpx.Response(200, json={"answers": [
        {"type": "predicate", "name": "unsafe_request", "probability": unsafe},
        {"type": "predicate", "name": "in_support_scope", "probability": relevant},
    ]})


@pytest.mark.parametrize("kind", ["unsafe", "irrelevant", "malformed", "error", "timeout", "history_error"])
def test_api_refusal_or_failure_starts_no_graph_and_writes_nothing(application, offline_admission, monkeypatch, kind):
    def respond(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("Timed out", request=request)
        if kind == "error":
            return httpx.Response(503, json={"error": {"message": "Unavailable"}})
        if kind == "malformed":
            return answer(unsafe="0.01")
        return answer(unsafe=1.0 if kind == "unsafe" else 0.0, relevant=0.0)

    offline_admission.handler = respond
    if kind == "history_error":
        monkeypatch.setattr(application.memory, "aget_tuple", AsyncMock(side_effect=OperationalError("read failed")))
    policy = load_guard_config(load_tenant()).policy
    expected = {"unsafe": policy.unsafe_reply, "irrelevant": policy.irrelevant_reply}.get(kind, policy.unavailable_reply)
    with TestClient(application.app) as browser:
        result = browser.post("/chat", json={"message": "Untrusted request"})
        assert result.status_code == 200
        assert result.json() == {"reply": expected, "blocks": [{"type": "text", "text": expected}]}
        assert application.calls == application.memory.operations == []
        assert len(offline_admission.calls) == (0 if kind == "history_error" else 1)
        assert not offline_admission.clients[0].is_closed()
    assert all(client.is_closed() for client in offline_admission.clients)
    assert application.closed == application.graphs


def test_approved_requests_use_one_guard_client_and_history_without_rejected_turns(application, offline_admission):
    def respond(request):
        message = json.loads(json.loads(request.content)["input"])["current_request"]
        return answer(relevant=0.0 if message == "Reject" else 1.0)
    offline_admission.handler = respond
    with TestClient(application.app) as browser:
        assert browser.post("/chat", json={"message": "First"}).json()["reply"] == "Approved reply"
        count = len(application.memory.operations)
        browser.post("/chat", json={"message": "Reject"})
        assert len(application.memory.operations) == count
        assert browser.post("/chat", json={"message": "Another"}).json()["reply"] == "Approved reply"
        evidence = json.loads(offline_admission.calls[-1]["input"])
        assert "First" in evidence["history"] and "Approved reply" in evidence["history"]
        assert "Reject" not in evidence["history"]
        assert evidence["current_request"] == "Another"
        assert [request.message for request in application.calls] == ["First", "Another"]
        assert len(application.graphs) == len(offline_admission.clients) == 1
        assert len(offline_admission.calls) == 3
        assert offline_admission.clients[0].max_retries == 0


def test_api_does_not_repeat_admission_or_graph_on_database_failure(application, offline_admission):
    application.settings.fail_graph = True
    with TestClient(application.app) as browser:
        assert browser.post("/chat", json={"message": "Hi"}).status_code == 500
        assert len(application.calls) == len(offline_admission.calls) == 1


def test_cli_uses_same_guard_and_refusal_never_enters_graph(application, offline_admission, monkeypatch, capsys):
    offline_admission.handler = lambda _: answer(relevant=0.0)
    original = bootstrap.open_chat_workflow
    monkeypatch.setattr(bootstrap, "open_chat_workflow", lambda: original(
        checkpointer=application.memory, graph_factory=application.graph_factory,
    ))
    messages = iter(["Unrelated", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(messages))
    assert bootstrap.main(["chat"]) == 0
    assert load_guard_config(load_tenant()).policy.irrelevant_reply in capsys.readouterr().out
    assert application.calls == application.memory.operations == []
    assert len(offline_admission.calls) == 1
    assert offline_admission.clients[0].is_closed()


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"history_messages": -1},
    {"history_characters": 50001}, {"enabled": False}, {"evaluator": "missing"},
])
def test_invalid_guard_config_fails_before_checkpoint_pool_open(tmp_path, monkeypatch, change):
    values = load_guard_config(load_tenant()).model_dump()
    values.update(change)
    path = tmp_path / "guard.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv("GUARD_CONFIG", str(path))
    pool = MagicMock(side_effect=AssertionError("Pool should not open"))
    monkeypatch.setattr(api, "AsyncConnectionPool", pool)
    with pytest.raises(ServiceConfigurationError):
        with TestClient(api.create_http_app()):
            pytest.fail("Invalid guard policy started")
    pool.assert_not_called()


def test_guard_config_file_precedence_and_client_snapshot(tmp_path):
    first, second = load_tenant("clinic_001"), load_tenant("clinic_002")
    default = load_guard_config(first).model_dump()
    (tmp_path / "guard.json").write_text(json.dumps(default), encoding="utf-8")
    override = tmp_path / "dietitians" / first.dietitian_id / "guard.json"
    override.parent.mkdir(parents=True)
    changed = {**default, "policy": {**default["policy"], "support_scope": "Client-specific scope"}}
    override.write_text(json.dumps(changed), encoding="utf-8")
    assert load_guard_config(first, config_dir=tmp_path, environ={}).policy.support_scope == "Client-specific scope"
    assert load_guard_config(second, config_dir=tmp_path, environ={}).policy.support_scope == default["policy"]["support_scope"]
    assert load_guard_config(first, config_dir=tmp_path, environ={"GUARD_CONFIG": str(tmp_path / "guard.json")}).policy.support_scope == default["policy"]["support_scope"]


def test_history_is_scoped_and_bounded_without_loading_prompts_or_media():
    client = bootstrap.client_for_tenant(load_tenant())
    calls = []
    async def read(config):
        calls.append(config)
        return SimpleNamespace(checkpoint={"channel_values": {"messages": [
            HumanMessage(content="Old text"),
            SystemMessage(content="Private prompt"),
            HumanMessage(content="Question"),
            AIMessage(content=[
                {"type": "image_url", "image_url": {"url": "data:private-image"}},
                {"type": "text", "text": "A" * 500},
            ]),
        ]}})
    loader = CheckpointHistoryLoader(SimpleNamespace(aget_tuple=read), client=client, max_messages=3, max_characters=30)
    async def run():
        request = ConversationRequest(client_id=client.client_id, conversation_id="session", message="Again")
        history = await loader(request)
        assert len(history) <= 30
        assert "assistant:" in history
        assert all(text not in history for text in ("Old text", "Private prompt", "private-image"))
        assert calls == [{"configurable": {"thread_id": "clinic_001:session"}}]
        with pytest.raises(ValueError, match="client/release"):
            await loader(request.model_copy(update={"client_id": "clinic_002"}))
        assert len(calls) == 1
    asyncio.run(run())


def test_guard_policy_is_frozen_for_runtime_and_client_survives_until_eviction(application, offline_admission):
    async def run():
        from backend.workflows.facade import WorkflowFacade
        first, second = load_tenant("clinic_001"), load_tenant("clinic_002")
        a, b = bootstrap.client_for_tenant(first), bootstrap.client_for_tenant(second)
        config = load_guard_config(first)
        factories = {context.client_id: bootstrap.create_workflow_factory(
            application.memory, tenant=tenant, guard_config=config, graph_factory=application.graph_factory,
        ) for context, tenant in ((a, first), (b, second))}
        async with WorkflowFacade(lambda client: factories[client.client_id](client), max_cached_workflows=1) as facade:
            async with facade.cache.acquire(a) as workflow:
                assert isinstance(workflow, GuardedWorkflow)
                assert workflow.guard.policy == config.policy
                assert workflow.history_loader.client == a
            assert not offline_admission.clients[0].is_closed()
            await facade.warm(b)
            assert offline_admission.clients[0].is_closed()
            assert not offline_admission.clients[1].is_closed()
        assert all(client.is_closed() for client in offline_admission.clients)
    asyncio.run(run())
