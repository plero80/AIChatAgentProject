"""Behavior to preserve when the Python graph is later assembled from JSON.

Run the real builder, graph, nodes, and prompt/presentation services. Only model
responses and recipe search results are scripted; no provider or database is used.
"""

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from backend.graphs.builders.recipe_chat_builder import RecipeChatBuilder
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.recipe_chat_graph import RecipeChatGraph
from backend.graphs.compiled_recipe_chat_graph import CompiledRecipeChatGraph
from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe import Recipe
from backend.models.recipe_presentation_plan import RecipePresentationPlan
from backend.services.chat.dietitian_service import DietitianService
from backend.services.chat.message_analyzer import MessageAnalyzer
from backend.services.chat.recipe_generation_service import RecipeGenerationService
from backend.services.chat.recipe_presentation_service import RecipePresentationService
from backend.tenancy import load_tenant
from backend.clients.context import ClientContext
from backend.workflows.adapters.recipe import RecipeWorkflowAdapter
from backend.workflows.contracts import ConversationRequest
from backend.config.guard import load_guard_config
from backend.guards.contracts import GuardAssessment, GuardEvaluator
from backend.guards.history import CheckpointHistoryLoader
from backend.guards.request_guard import RequestGuard
from backend.workflows.guarded import GuardedWorkflow


class ScriptedModel:
    """Fixed model outputs, with unexpected calls failing instead of going online."""

    def __init__(self):
        self.replies = deque()
        self.calls = []

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        assert self.replies, "Unexpected model call: no baseline reply was supplied"
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(params=[RecipeChatGraph, CompiledRecipeChatGraph], ids=["python", "json"])
def baseline(request):
    tenant = load_tenant("clinic_001")
    models = SimpleNamespace(**{
        role: ScriptedModel()
        for role in ("analysis", "chat", "generation", "response")
    })
    recipes = [
        Recipe(
            id=UUID(int=number),
            name=name,
            instructions=f"Saved instructions for {name}.",
            image_url=f"/recipe-images/{name}.jpg",
            instagram_url=f"https://www.instagram.com/p/{name}/",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for number, name in ((1, "tofu"), (2, "rolls"))
    ]
    search = SimpleNamespace(find_best_recipes=AsyncMock(return_value=recipes))
    nodes = (
        RecipeChatBuilder(tenant=tenant)
        .analysisNode(MessageAnalyzer(models.analysis, tenant=tenant))
        .recipeNode(RecipeGenerationService(models.generation, tenant=tenant), search)
        .chatNode(models.chat)
        .responseNode(models.response, RecipePresentationService())
        .dietitianNode(DietitianService(tenant=tenant))
        .build()
    )
    return SimpleNamespace(
        graph=request.param(nodes, checkpointer=InMemorySaver(), tenant=tenant),
        tenant=tenant,
        models=models,
        search=search,
        recipes=recipes,
    )


def request(message):
    return ChatGraphRequest(message, "baseline-session", "clinic_001")


def selection(baseline):
    # Select the second result to catch accidental presentation of every hit.
    return RecipePresentationPlan(
        intro="Here is the selected recipe.",
        recipe_ids=[str(baseline.recipes[1].id)],
        outro="You can ask about tofu next.",
    )


@pytest.mark.parametrize(
    "message,intent,recipe_mode,expected_mode,search_calls,generation_calls,chat_calls",
    [
        pytest.param("Hello", "chat", "none", "chat", 0, 0, 1, id="chat"),
        pytest.param("Find saved rolls", "food", "existing", "existing", 1, 0, 0, id="saved-recipe"),
        pytest.param("I have tofu", "food", "none", "existing", 1, 0, 0, id="food-defaults-to-search"),
        pytest.param("A dish in her style", "food", "inspired", "inspired", 1, 1, 0, id="inspired-recipe"),
        pytest.param("Invent a dish", "food", "original", "original", 0, 1, 0, id="original-recipe"),
        pytest.param("Find a missing recipe", "food", "existing", "not_found", 1, 0, 0, id="no-search-results"),
        pytest.param("Contact the dietitian", "dietitian_escort", "none", "dietitian_escort", 0, 0, 0, id="contact"),
    ],
)
def test_turn_baseline(
    baseline, message, intent, recipe_mode, expected_mode,
    search_calls, generation_calls, chat_calls,
):
    baseline.models.analysis.replies.append(MessageAnalysis(
        intent=intent, recipe_mode=recipe_mode,
        ingredients=["tofu"], preferences=["quick"],
    ))
    if chat_calls:
        baseline.models.chat.replies.append(AIMessage(content="Internal chat draft"))
    if generation_calls:
        baseline.models.generation.replies.append(AIMessage(content="Generated dish"))
    if expected_mode == "not_found":
        baseline.search.find_best_recipes.return_value = []
    baseline.models.response.replies.append(
        selection(baseline) if expected_mode == "existing"
        else AIMessage(content="Displayed answer")
    )

    result = asyncio.run(baseline.graph.arun(request(message)))

    assert result["result"]["mode"] == expected_mode
    assert result.get("validation") is None
    assert baseline.search.find_best_recipes.await_count == search_calls
    assert len(baseline.models.generation.calls) == generation_calls
    assert len(baseline.models.chat.calls) == chat_calls
    if search_calls:
        search_input = baseline.search.find_best_recipes.call_args.kwargs
        assert search_input["user_text"] == message
        assert search_input["plan"].ingredients == ["tofu"]
        assert search_input["plan"].preferences == ["quick"]
    if generation_calls:
        assert result["result"]["text"] == "Generated dish"
        assert "Generated dish" in str(baseline.models.response.calls[-1])
        if expected_mode == "inspired":
            assert baseline.recipes[0].instructions in baseline.models.generation.calls[0]
    if expected_mode == "not_found":
        assert result["result"]["recipes"] == []
    if expected_mode == "dietitian_escort":
        assert str(baseline.tenant.profile.contact_url) in result["result"]["text"]

    if expected_mode == "existing":
        cards = [block["recipe"] for block in result["response_blocks"] if block["type"] == "recipe"]
        assert [card["id"] for card in cards] == [str(baseline.recipes[1].id)]
        assert cards[0]["image_url"] == "/recipe-images/rolls.jpg"
        assert cards[0]["instructions"] == baseline.recipes[1].instructions
        assert cards[0]["instagram_url"] == baseline.recipes[1].instagram_url
        assert "/recipe-images/rolls.jpg" in result["final_response"]
        assert "/recipe-images/tofu.jpg" not in str(result["response_blocks"])
        assert "/recipe-images/tofu.jpg" not in result["final_response"]
    else:
        assert result["response_blocks"] == [{"type": "text", "text": "Displayed answer"}]
    assert result["messages"][-1].content == result["final_response"]


def guarded(baseline, evaluate):
    class Evaluator(GuardEvaluator):
        async def evaluate(self, request, *, policy, history):
            return await evaluate(request, history)

    client = ClientContext(client_id="clinic_001", display_name="Clinic")
    policy = load_guard_config(baseline.tenant).policy
    return GuardedWorkflow(
        RecipeWorkflowAdapter(baseline.graph, client=client),
        RequestGuard(client=client, policy=policy, evaluator=Evaluator()),
        history_loader=CheckpointHistoryLoader(baseline.graph.memory, client=client, max_messages=6, max_characters=6000),
    )


def public_request(message):
    return ConversationRequest(message=message, client_id="clinic_001", conversation_id="baseline-session")


def test_followup_preserves_approved_history_and_rejection_writes_no_checkpoint(baseline):
    baseline.models.analysis.replies.extend([
        MessageAnalysis(intent="food", recipe_mode="existing"),
        MessageAnalysis(intent="chat", recipe_mode="none"),
    ])
    baseline.models.chat.replies.append(AIMessage(content="Internal draft"))
    baseline.models.response.replies.extend([
        selection(baseline), AIMessage(content="Final follow-up"),
    ])

    async def conversation():
        async def evaluate(request, history):
            if request.message == "Unavailable":
                raise RuntimeError("Evaluator failed")
            return GuardAssessment(unsafe_probability=0.0, relevant_probability=0.0 if request.message == "Unrelated request" else 1.0)
        workflow = guarded(baseline, evaluate)
        first = await workflow.respond(public_request("Show saved rolls"))
        config = {"configurable": {"thread_id": "clinic_001:baseline-session"}}
        before = await baseline.graph.memory.aget_tuple(config)
        rejected = await workflow.respond(public_request("Unrelated request"))
        unavailable = await workflow.respond(public_request("Unavailable"))
        after = await baseline.graph.memory.aget_tuple(config)
        assert before.checkpoint["id"] == after.checkpoint["id"]
        final = await workflow.respond(public_request("Thanks"))
        saved = await baseline.graph.graph.aget_state(config)
        return first, rejected, unavailable, final, saved.values

    first, rejected, unavailable, final, state = asyncio.run(conversation())

    policy = load_guard_config(baseline.tenant).policy
    assert rejected.reply == policy.irrelevant_reply
    assert unavailable.reply == policy.unavailable_reply
    assert all(block.type == "text" for block in rejected.blocks)
    assert state["result"]["mode"] == "chat"
    assert state["response_blocks"] == [{"type": "text", "text": "Final follow-up"}]
    assert state["messages"][-1].content == final.reply
    assert [m.content for m in state["messages"] if isinstance(m, HumanMessage)] == [
        "Show saved rolls", "Thanks",
    ]
    history = baseline.models.analysis.calls[-1][-1].content
    assert first.reply in history
    assert rejected.reply not in history
    assert "Unrelated request" not in history
    assert "/recipe-images/tofu.jpg" not in history
    assert len(baseline.models.response.calls) == 2


def test_rejection_never_starts_analysis_or_a_speculative_branch(baseline):
    async def conversation():
        started = asyncio.Event()
        release = asyncio.Event()
        async def evaluate(request, history):
            started.set()
            await release.wait()
            return GuardAssessment(unsafe_probability=1.0, relevant_probability=1.0)
        workflow = guarded(baseline, evaluate)
        task = asyncio.create_task(workflow.respond(public_request("Reject this turn")))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert baseline.models.analysis.calls == baseline.models.chat.calls == []
            release.set()
            result = await asyncio.wait_for(task, timeout=5)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert await baseline.graph.memory.aget_tuple({"configurable": {"thread_id": "clinic_001:baseline-session"}}) is None
        return result

    result = asyncio.run(conversation())

    assert result.reply == load_guard_config(baseline.tenant).policy.unsafe_reply
    assert baseline.models.analysis.calls == baseline.models.chat.calls == []
    assert not baseline.models.response.calls


def test_adapter_preserves_real_recipe_presentation_and_followup_checkpoints(baseline):
    baseline.models.analysis.replies.extend([
        MessageAnalysis(intent="food", recipe_mode="existing"),
        MessageAnalysis(intent="chat", recipe_mode="none"),
    ])
    baseline.models.chat.replies.append(AIMessage(content="Internal draft"))
    baseline.models.response.replies.extend([selection(baseline), AIMessage(content="You're welcome")])
    adapter = RecipeWorkflowAdapter(baseline.graph, client=ClientContext(
        client_id=baseline.tenant.dietitian_id, display_name="Clinic",
    ))

    async def conversation():
        responses = []
        for message in ("Show saved rolls", "Thanks"):
            responses.append(await adapter.respond(ConversationRequest(
                message=message, client_id="clinic_001", conversation_id="baseline-session",
            )))
        checkpoint = await baseline.graph.graph.aget_state(
            {"configurable": {"thread_id": "clinic_001:baseline-session"}},
        )
        return responses, checkpoint.values

    (first, followup), state = asyncio.run(conversation())

    card = next(block.recipe for block in first.blocks if block.type == "recipe")
    assert card.id == str(baseline.recipes[1].id)
    assert card.image_url == "/recipe-images/rolls.jpg"
    assert card.instructions == baseline.recipes[1].instructions
    assert followup.model_dump() == {
        "reply": "You're welcome", "blocks": [{"type": "text", "text": "You're welcome"}],
    }
    assert state["messages"][-1].content == followup.reply
    assert first.reply in [message.content for message in state["messages"]]
    assert state["response_blocks"] == followup.model_dump()["blocks"]


def test_recipe_build_trace_records_router_reducer_and_checkpoint_storage(baseline):
    records = [json.loads(line) for line in baseline.graph.build_trace_path.read_text(encoding="utf-8").splitlines()]
    assert sum(r["event"] == "build_started" for r in records) == 1
    assert sum(r["event"] == "build_succeeded" for r in records) == 1
    assert len({r["build_id"] for r in records}) == 1
    assert {r["client_id"] for r in records} == {baseline.tenant.dietitian_id}
    expected_source = "json" if isinstance(baseline.graph, CompiledRecipeChatGraph) else "python"
    assert {r["source"] for r in records} == {expected_source}
    assert {r["node_id"] for r in records if r["event"] == "node_added"} == (
        {"analyze", "chat", "recipe", "dietitian", "respond"} if expected_source == "json" else {"analyze", "chat", "recipe", "dietitian", "response"}
    )
    route = next(r for r in records if r["event"] == "route_added")
    assert route["from_node"] == "analyze"
    assert route["paths"] == {"chat": "chat", "recipe": "recipe", "dietitian": "dietitian"}
    messages = next(r for r in records if r["event"] == "state_field" and r["name"] == "messages")
    assert messages["reducer"] is not None
    compile_stage = next(r for r in records if r["event"] == "stage_started" and r["phase"] == "compile")
    assert compile_stage["checkpointer"].endswith("InMemorySaver")
