"""The chat boundary works without a recipe state, builder, or external service."""

import asyncio
from argparse import Namespace
from typing import TypedDict
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

import backend.web.api as api
from backend.clients.context import ClientContext
from backend.cli.commands.chat_command import ChatCommand
from backend.graphs.graph_requests import ChatGraphRequest
from backend.models.chat_response import ChatResponse, TextBlock
from backend.workflows.adapters.recipe import RecipeWorkflowAdapter
from backend.workflows.contracts import ChatWorkflow, ConversationRequest


class CounterState(TypedDict):
    message: str
    count: int


class CounterWorkflow(ChatWorkflow):
    """An example domain-independent workflow with its own private state."""

    def __init__(self, client, checkpointer=None):
        super().__init__(client)
        self.requests = []
        builder = StateGraph(CounterState)
        builder.add_node("count", lambda state: {"count": state.get("count", 0) + 1})
        builder.add_edge(START, "count")
        builder.add_edge("count", END)
        self.graph = builder.compile(checkpointer=checkpointer or InMemorySaver())

    async def _respond(self, request):
        self.requests.append(request)
        state = await self.graph.ainvoke(
            {"message": request.message},
            config={"configurable": {"thread_id": request.thread_id}},
        )
        reply = f"{state['count']}: {state['message']}"
        return ChatResponse(reply=reply, blocks=[TextBlock(text=reply)])


def turn(message="Hi", *, client_id="chef_001", conversation_id="session-a", release_id="legacy"):
    return ConversationRequest(
        message=message, client_id=client_id,
        conversation_id=conversation_id, release_id=release_id,
    )


def test_context_and_request_are_immutable_and_workflow_requires_an_implementation():
    client = ClientContext(client_id="chef_001", display_name="Chef")
    request = turn()
    with pytest.raises(ValidationError, match="frozen"):
        client.client_id = "other"
    with pytest.raises(ValidationError, match="frozen"):
        request.conversation_id = "other"
    with pytest.raises(TypeError):
        ChatWorkflow(client)
    assert request.thread_id == "chef_001:session-a"


@pytest.mark.parametrize("changes", [
    {"client_id": "chef_002"},
    {"release_id": "another-release"},
])
def test_identity_mismatch_is_rejected_before_workflow_execution(changes):
    workflow = CounterWorkflow(ClientContext(client_id="chef_001", display_name="Chef"))
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(workflow.respond(turn(**changes)))
    assert workflow.requests == []


def test_private_state_and_shared_checkpointer_remain_isolated_by_client_and_conversation():
    memory = InMemorySaver()
    first = CounterWorkflow(ClientContext(client_id="chef_001", display_name="First"), memory)
    second = CounterWorkflow(ClientContext(client_id="chef_002", display_name="Second"), memory)

    async def conversation():
        a, b, c = await asyncio.gather(
            first.respond(turn()),
            first.respond(turn(conversation_id="session-b")),
            second.respond(turn(client_id="chef_002")),
        )
        assert a.reply == b.reply == c.reply == "1: Hi"
        return await first.respond(turn("Again"))

    response = asyncio.run(conversation())
    assert response.model_dump() == {
        "reply": "2: Again", "blocks": [{"type": "text", "text": "2: Again"}],
    }


def test_api_and_cli_accept_a_workflow_with_no_recipe_nodes(monkeypatch, capsys):
    workflow = CounterWorkflow(ClientContext(
        client_id="chef_001", display_name="Chef", release_id="test-release",
    ))
    application = api.create_http_app()
    application.state.workflow = workflow
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)
    # Supply an already built workflow; do not run the deployment/database lifespan.
    client = TestClient(application)
    try:
        for message, reply in (("Hi", "1: Hi"), ("Again", "2: Again")):
            result = client.post("/chat", json={"message": message})
            assert result.status_code == 200
            assert result.json() == {"reply": reply, "blocks": [{"type": "text", "text": reply}]}
        assert workflow.requests[0].conversation_id == workflow.requests[1].conversation_id
    finally:
        client.close()

    messages = iter(["Hello", "Again", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(messages))
    assert asyncio.run(ChatCommand(lambda: workflow).run(Namespace(user_id="terminal"))) == 0
    output = capsys.readouterr().out
    assert "Chef: 1: Hello" in output
    assert "Chef: 2: Again" in output
    assert all(request.client_id == "chef_001" for request in workflow.requests)
    assert all(request.release_id == "test-release" for request in workflow.requests)


@pytest.mark.parametrize("state,expected", [
    ({"final_response": "Hello"}, {"reply": "Hello", "blocks": [{"type": "text", "text": "Hello"}]}),
    ({"final_response": "Hello", "response_blocks": None}, {"reply": "Hello", "blocks": [{"type": "text", "text": "Hello"}]}),
    ({"final_response": "Hello", "response_blocks": []}, {"reply": "Hello", "blocks": []}),
    ({}, {"reply": "", "blocks": []}),
])
def test_recipe_adapter_preserves_presentation_fallbacks_and_legacy_thread_identity(state, expected):
    graph = AsyncMock()
    graph.arun.return_value = state
    adapter = RecipeWorkflowAdapter(graph, client=ClientContext(client_id="clinic_001", display_name="Clinic"))
    request = turn(client_id="clinic_001")

    response = asyncio.run(adapter.respond(request))

    assert response.model_dump() == expected
    legacy = ChatGraphRequest(message="Hi", user_id="session-a", dietitian_id="clinic_001")
    graph.arun.assert_awaited_once_with(legacy)
    assert request.thread_id == legacy.thread_id


def test_internal_state_cannot_be_returned_in_place_of_the_public_response():
    class BrokenWorkflow(ChatWorkflow):
        async def _respond(self, request):
            return {"private_count": 1}

    workflow = BrokenWorkflow(ClientContext(client_id="chef_001", display_name="Chef"))
    with pytest.raises(ValidationError):
        asyncio.run(workflow.respond(turn()))


@pytest.mark.parametrize("error", [RuntimeError("graph failed"), asyncio.CancelledError()])
def test_recipe_adapter_propagates_graph_failure_and_cancellation(error):
    graph = AsyncMock()
    graph.arun.side_effect = error
    adapter = RecipeWorkflowAdapter(graph, client=ClientContext(client_id="clinic_001", display_name="Clinic"))
    with pytest.raises(type(error)):
        asyncio.run(adapter.respond(turn(client_id="clinic_001")))
    assert graph.arun.await_count == 1


def test_chat_without_an_initialized_workflow_returns_service_unavailable():
    client = TestClient(api.create_http_app())
    try:
        response = client.post("/chat", json={"message": "Hi"})
        assert response.status_code == 503
    finally:
        client.close()
