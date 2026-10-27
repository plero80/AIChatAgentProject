import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from backend.clients.manage import export_legacy, read_release
from backend.clients.releases import ClientRecord, PublishedRelease, ReleaseSpec
from backend.config.services import ServiceConfigurationError
from backend.config.workflows import WorkflowConfigurationError
from backend.paths import CONFIG_DIR
from backend.workflows.contracts import ConversationRequest
from backend.workflows.checkpoint_types import checkpoint_serializer
from backend.workflows.facade import WorkflowFacade
from backend.workflows.release_runtime import create_release_workflow_factory, validate_release


@pytest.mark.parametrize("mutation", [
    lambda d: d["workflow"]["nodes"][0]["bindings"]["writer"].update(implementation="import_python"),
    lambda d: d["workflow"]["nodes"][0]["bindings"].update(database={"implementation": "postgres"}),
    lambda d: d["workflow"]["nodes"][0]["bindings"]["writer"].update(model="missing"),
    lambda d: d["workflow"]["nodes"][0]["bindings"]["writer"].update(model=None),
    lambda d: d["workflow"]["nodes"][0]["bindings"]["writer"].update(prompts={"system": "missing"}),
    lambda d: d["workflow"]["nodes"][0]["bindings"]["writer"].update(prompts={"analyze": "chef_support"}),
    lambda d: d["prompts"]["chef_support"].update(text="Read $secret_database_url"),
    lambda d: d["workflow"]["nodes"][0].update(needs=["analysis"]),
    lambda d: d["workflow"]["edges"][1].update(to="respond"),
    lambda d: d["models"]["writer"].update(provider="arbitrary_provider"),
])
def test_invalid_bindings_fail_before_any_resource_is_created(release_document, release_factories, mutation):
    data = release_document().model_dump(mode="json", by_alias=True)
    mutation(data)
    with pytest.raises((WorkflowConfigurationError, ServiceConfigurationError, ValidationError)):
        validate_release(ReleaseSpec.model_validate(data), factories=release_factories.factories)
    assert not release_factories.models


def test_examples_and_legacy_export_are_valid_without_network():
    read_release(CONFIG_DIR / "releases" / "chef.example.json")
    michael = read_release(CONFIG_DIR / "releases" / "michael_lipkin.json")
    assert michael.client_id == "michael_lipkin"
    assert michael.profile.display_name == "Michael Lipkin"
    assert michael.image_recognition is not None
    exported = export_legacy("clinic_001")
    assert exported.client_id == "clinic_001"
    assert all(node.bindings for node in exported.workflow.nodes)
    assert exported.workflow.nodes[0].bindings["analyzer"].prompts == {"analyze": "analyze"}


def test_cache_eviction_keeps_checkpoint_and_published_prompt_model_selection(release_document, release_factories):
    async def scenario():
        first = PublishedRelease(ClientRecord("chef_demo", "chef", "chef", "one"), "one", 1, release_document())
        second = replace(first, release_id="two", version=2, spec=release_document(version=2, model="writer-two"))
        releases = {"one": first, "two": second}
        catalog = SimpleNamespace(get_release=AsyncMock(side_effect=lambda c, r: releases[r]))
        memory = InMemorySaver()
        dependencies = AsyncMock(side_effect=AssertionError("Text workflows must not open recipe storage"))
        factory = create_release_workflow_factory(catalog, memory, factories=release_factories.factories, dependencies=dependencies)
        async with WorkflowFacade(factory, max_cached_workflows=1) as facade:
            async def send(release, session):
                return await facade.respond(release.context, ConversationRequest(
                    message="Plan dinner", client_id="chef_demo", conversation_id=session, release_id=release.release_id,
                ))
            assert (await send(first, "old")).reply == "writer-one: turn 1"
            assert (await send(second, "new")).reply == "writer-two: turn 1"
            assert (await send(first, "old")).reply == "writer-one: turn 2"
            assert [messages[0].content for _, messages in release_factories.calls] == [
                "Published instructions v1 for Demo Chef", "Published instructions v2 for Demo Chef",
                "Published instructions v1 for Demo Chef",
            ]
        assert sorted(release_factories.closed) == ["writer-one", "writer-one", "writer-two"]
        assert not dependencies.called
    asyncio.run(scenario())


def test_failed_release_build_closes_created_resources(release_document, release_factories):
    async def scenario():
        release = PublishedRelease(ClientRecord("chef_demo", "chef", "chef", "one"), "one", 1,
                                   release_document(model="broken"))
        catalog = SimpleNamespace(get_release=AsyncMock(return_value=release))
        factory = create_release_workflow_factory(catalog, InMemorySaver(), factories=release_factories.factories)
        async with WorkflowFacade(factory) as facade:
            with pytest.raises(RuntimeError, match="Construction failed"):
                await facade.warm(release.context)
        assert release_factories.closed == ["broken"]
    asyncio.run(scenario())


def test_recipe_bindings_inject_independent_services_prompts_and_models(release_factories):
    from langchain_core.messages import AIMessage, HumanMessage
    from backend.graphs.graph_requests import ChatGraphRequest
    from backend.graphs.recipe_chat_graph import RecipeChatGraph
    from backend.models.message_analysis import MessageAnalysis
    from backend.services.contracts import BaseMessageAnalyzer, BaseRecipeSearchService
    from backend.tenancy import load_tenant

    seen = []

    class Analyzer(BaseMessageAnalyzer):
        def __init__(self, *, llm, tenant):
            seen.append((llm.label, tenant.render("analyze")))

        async def analyze(self, message, current_user_message=None):
            return MessageAnalysis(intent="chat", recipe_mode="none", ingredients=[], preferences=[])

    class Search(BaseRecipeSearchService):
        async def find_best_recipes(self, user_text, plan, top_k=5):
            raise AssertionError("Chat intent must not execute recipe search")

    release_factories.factories.message_analyzer.register("scripted", Analyzer)
    release_factories.factories.recipe_search.register("empty", lambda **deps: Search())
    data = export_legacy("clinic_001").model_dump(mode="json", by_alias=True)
    for key in data["models"]:
        data["models"][key] = {"provider": "local", "model": key, "max_tokens": 100}
    data["prompts"]["analyze"]["text"] = "Release-specific instructions for $display_name"
    for node in data["workflow"]["nodes"]:
        if node["kind"] == "recipe_analysis":
            node["bindings"]["analyzer"]["implementation"] = "scripted"
        if node["kind"] == "recipe_execute":
            node["bindings"]["search"]["implementation"] = "empty"
    spec = ReleaseSpec.model_validate(data)
    release = PublishedRelease(ClientRecord("clinic_001", "clinic", "dietitian", "one"), "one", 1, spec)

    async def scenario():
        memory = InMemorySaver(serde=checkpoint_serializer())
        # A completed native Python graph checkpoint must remain readable after
        # its conversation is adopted by the initial published JSON release.
        old_nodes = SimpleNamespace(
            analysis=lambda state: {"analysis": MessageAnalysis(intent="chat", recipe_mode="none")},
            chat=lambda state: {"result": {"mode": "chat", "text": "Legacy reply"}},
            recipe=lambda state: {}, dietitian=lambda state: {},
            response=lambda state: {"final_response": "Legacy reply", "response_blocks": [],
                                    "messages": [AIMessage(content="Legacy reply")]},
        )
        old_graph = RecipeChatGraph(old_nodes, tenant=load_tenant("clinic_001"), checkpointer=memory)
        await old_graph.arun(ChatGraphRequest("Earlier message", "recipe-session", "clinic_001"))
        factory = create_release_workflow_factory(
            SimpleNamespace(get_release=AsyncMock(return_value=release)), memory,
            factories=release_factories.factories, dependencies=AsyncMock(return_value={}),
        )
        async with WorkflowFacade(factory) as facade:
            answer = await facade.respond(release.context, ConversationRequest(
                client_id="clinic_001", conversation_id="recipe-session", release_id="one", message="Hello",
            ))
            assert answer.reply.startswith("writer:")
        assert seen == [("classifier", f"Release-specific instructions for {spec.profile.display_name}")]
        assert [label for label, _ in release_factories.calls] == ["writer", "writer"]
        assert [message.content for message in release_factories.calls[0][1] if isinstance(message, HumanMessage)] == [
            "Earlier message", "Hello",
        ]
        assert len(release_factories.closed) == len(release_factories.models)
    asyncio.run(scenario())


def test_non_llm_bindings_reject_model_and_prompt_injection():
    for slot, extra in (("search", {"model": "writer"}), ("presentation", {"prompts": {"system": "chat"}})):
        data = export_legacy("clinic_001").model_dump(mode="json", by_alias=True)
        for node in data["workflow"]["nodes"]:
            if slot in node["bindings"]:
                node["bindings"][slot].update(extra)
        with pytest.raises(WorkflowConfigurationError):
            validate_release(ReleaseSpec.model_validate(data))


def test_checkpoint_serializer_restores_only_registered_domain_types():
    from backend.models.message_analysis import MessageAnalysis
    from backend.models.request_validation import RequestValidation

    serializer = checkpoint_serializer()
    for value in (MessageAnalysis(intent="chat", recipe_mode="none", ingredients=[], preferences=[]),
                  RequestValidation(legitimate=True)):
        assert serializer.loads_typed(serializer.dumps_typed(value)) == value
