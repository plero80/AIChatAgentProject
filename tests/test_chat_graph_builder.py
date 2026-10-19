import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from backend.graphs.builders.recipe_chat_builder import RecipeChatBuilder
from backend.graphs.recipe_chat_graph import RecipeChatGraph
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.recipe_chat_nodes import RecipeChatNodes
from backend.models.message_analysis import MessageAnalysis
from backend.models.request_validation import RequestValidation
from backend.services.chat.recipe_presentation_service import RecipePresentationService


@pytest.fixture
def dependencies():
    return SimpleNamespace(
        analyzer=SimpleNamespace(analyze=AsyncMock(
            return_value=MessageAnalysis(intent="chat", recipe_mode="none"),
        )),
        validator=SimpleNamespace(validate=AsyncMock(
            return_value=RequestValidation(legitimate=True),
        )),
        generation=MagicMock(),
        search=MagicMock(),
        chat_llm=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="Chat reply"))),
        response_llm=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="Final reply"))),
        presentation=RecipePresentationService(),
        dietitian=MagicMock(),
    )


def configure(builder, deps):
    # Guard configuration can come before its branch nodes.
    return (
        builder
        .guardNode(deps.validator)
        .analysisNode(deps.analyzer)
        .recipeNode(deps.generation, deps.search)
        .chatNode(deps.chat_llm)
        .responseNode(deps.response_llm, deps.presentation)
        .dietitianNode(deps.dietitian)
    )


def test_fluent_builder_returns_a_node_container_with_shared_branch_nodes(dependencies):
    builder = RecipeChatBuilder()
    assert configure(builder, dependencies) is builder
    nodes = builder.build()

    assert isinstance(nodes, RecipeChatNodes)
    assert nodes.analysis.analyzer is dependencies.analyzer
    assert nodes.recipe.recipe_generation_service is dependencies.generation
    assert nodes.recipe.recipe_search_service is dependencies.search
    assert nodes.chat.llm is dependencies.chat_llm
    assert nodes.response.llm is dependencies.response_llm
    assert nodes.response.presentation_service is dependencies.presentation
    assert nodes.dietitian.dietitian_service is dependencies.dietitian
    assert nodes.guard.validator is dependencies.validator
    assert nodes.guard.branches == {
        "chat": nodes.chat, "recipe": nodes.recipe, "dietitian": nodes.dietitian,
    }
    dependencies.analyzer.analyze.assert_not_called()
    dependencies.chat_llm.ainvoke.assert_not_called()
    with pytest.raises(FrozenInstanceError):
        nodes.chat = object()


@pytest.mark.parametrize("missing", [
    "analysisNode", "recipeNode", "chatNode", "responseNode", "dietitianNode", "guardNode",
])
def test_build_reports_unconfigured_nodes(dependencies, missing):
    builder = RecipeChatBuilder()
    steps = {
        "analysisNode": (dependencies.analyzer,),
        "recipeNode": (dependencies.generation, dependencies.search),
        "chatNode": (dependencies.chat_llm,),
        "responseNode": (dependencies.response_llm, dependencies.presentation),
        "dietitianNode": (dependencies.dietitian,),
        "guardNode": (dependencies.validator,),
    }
    for name, args in steps.items():
        if name != missing:
            getattr(builder, name)(*args)
    with pytest.raises(ValueError, match=missing):
        builder.build()


def test_reconfiguring_a_builder_does_not_change_an_existing_container(dependencies):
    builder = configure(RecipeChatBuilder(), dependencies)
    first = builder.build()
    replacement_llm = MagicMock()
    second = builder.chatNode(replacement_llm).build()

    assert first.chat.llm is dependencies.chat_llm
    assert second.chat.llm is replacement_llm
    assert first.guard.branches["chat"] is first.chat
    assert second.guard.branches["chat"] is second.chat
    assert first.guard is not second.guard


@pytest.mark.parametrize("use_supplied_checkpointer", [False, True])
def test_graph_runs_built_nodes_and_keeps_user_histories_separate(dependencies, use_supplied_checkpointer):
    nodes = configure(RecipeChatBuilder(), dependencies).build()
    supplied = InMemorySaver() if use_supplied_checkpointer else None
    graph = RecipeChatGraph(nodes=nodes, checkpointer=supplied)
    assert graph.nodes is nodes
    if use_supplied_checkpointer:
        assert graph.memory is supplied
    else:
        assert isinstance(graph.memory, InMemorySaver)

    async def conversation():
        a, b = await asyncio.gather(graph.arun(ChatGraphRequest("Hi!", "A")), graph.arun(ChatGraphRequest("Hello!", "B")))
        assert a["final_response"] == b["final_response"] == "Final reply"
        assert [message.content for message in a["messages"]] == ["Hi!", "Chat reply", "Final reply"]
        assert [message.content for message in b["messages"]] == ["Hello!", "Chat reply", "Final reply"]

    asyncio.run(conversation())
    follow_up = graph.run(ChatGraphRequest("Again", "A"))
    assert follow_up["response_blocks"] == [{"type": "text", "text": "Final reply"}]
    assert [message.content for message in follow_up["messages"]] == [
        "Hi!", "Chat reply", "Final reply", "Again", "Chat reply", "Final reply",
    ]
    assert graph.nodes is nodes
    assert dependencies.analyzer.analyze.await_count == 3
    assert dependencies.validator.validate.await_count == 3
    assert dependencies.chat_llm.ainvoke.await_count == 3
    assert dependencies.response_llm.ainvoke.await_count == 3
