import asyncio
import json
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

import backend.bootstrap as main
from backend.graphs.graph_requests import ChatGraphRequest, RecipeIngestionRequest
from backend.factories.model_registry import ModelRegistry
from backend.factories.service_factories import ServiceFactories
from backend.config.models import ModelAssignments, ModelConfig, ModelProfile
from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe_create import RecipeCreate
from backend.models.request_validation import RequestValidation
from backend.config.services import ServiceConfig, ServiceConfigurationError


class RecordingModel(BaseChatModel):
    label: str
    calls: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "recording"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append("text")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"Reply from {self.label}"))])

    def with_structured_output(self, schema, **kwargs):
        def run(messages):
            self.calls.append(schema.__name__)
            if schema is MessageAnalysis:
                if "invent" in str(messages[-1].content):
                    return MessageAnalysis(intent="food", recipe_mode="original")
                return MessageAnalysis(intent="chat", recipe_mode="none")
            if schema is RequestValidation:
                return RequestValidation(legitimate=True)
            if schema is RecipeCreate:
                return RecipeCreate(name=f"Extracted by {self.label}", description="Test", ingredients=[])
            raise AssertionError(f"Unexpected schema {schema}")
        return RunnableLambda(run)


def recording_factory():
    factories = ServiceFactories()
    created = []

    def build(*, profile):
        client = RecordingModel(label=profile.model)
        created.append(client)
        return client

    factories.chat_model.register("recording", build)
    return factories, created


def test_registry_reuses_assigned_profiles_and_skips_unused_profiles():
    factories, created = recording_factory()
    config = ModelConfig(
        models={
            name: ModelProfile(provider="recording", model=name)
            for name in ("default", "writer", "unused")
        },
        assignments=ModelAssignments(recipe_generation="writer", response="writer"),
    )
    registry = ModelRegistry(config, factories.chat_model)
    assert [model.label for model in created] == ["default", "writer"]
    assert registry.for_role("message_analyzer") is registry.for_role("request_validator")
    assert registry.for_role("response") is registry.for_role("recipe_generation")
    assert registry.for_role("response") is not registry.for_role("chat")
    assert set(registry.models) == {"default", "writer"}
    for _ in range(3):
        registry.for_role("response")
    assert len(created) == 2


def test_registry_validates_every_provider_before_constructing_any_client():
    factories, created = recording_factory()
    config = ModelConfig(models={
        "default": ModelProfile(provider="recording", model="valid"),
        "unused": ModelProfile(provider="missing", model="invalid"),
    })
    with pytest.raises(ServiceConfigurationError, match="Model profile 'unused'.*Unknown chat_model"):
        ModelRegistry(config, factories.chat_model)
    assert created == []


@pytest.mark.parametrize("builder", [main.create_app, main.create_ingest_recipe])
@pytest.mark.parametrize("content", [
    {"assignments": {"response": "unknown"}},
    {"models": {"unused": {"provider": "unknown", "model": "test"}}},
])
def test_bad_model_config_fails_before_external_dependencies(tmp_path, monkeypatch, builder, content):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setenv("MODELS_CONFIG", str(path))
    dependencies = MagicMock(side_effect=AssertionError("Dependency constructed"))
    monkeypatch.setattr(main, "get_required_services", dependencies)
    with pytest.raises(ServiceConfigurationError):
        builder(service_config=ServiceConfig())
    dependencies.assert_not_called()


def test_api_rejects_invalid_model_provider_before_opening_its_checkpoint_pool(monkeypatch):
    import backend.web.api as api
    config = ModelConfig(models={"default": ModelProfile(provider="unknown", model="test")})
    pool = MagicMock(side_effect=AssertionError("Database pool opened"))
    monkeypatch.setattr(api, "AsyncConnectionPool", pool)
    monkeypatch.setattr(api, "load_service_config", lambda: ServiceConfig())
    monkeypatch.setattr(api, "load_model_config", lambda **kwargs: config)

    async def start():
        async with api.lifespan(api.app):
            pytest.fail("API accepted an invalid model configuration")

    with pytest.raises(ServiceConfigurationError, match="Unknown chat_model"):
        asyncio.run(start())
    pool.assert_not_called()


def test_each_role_uses_its_profile_and_requests_reuse_the_clients(monkeypatch):
    factories, created = recording_factory()
    roles = list(ModelAssignments.model_fields)
    config = ModelConfig(
        models={role: ModelProfile(provider="recording", model=role) for role in roles},
        assignments=ModelAssignments(**{role: role for role in roles}),
    )
    embeddings = MagicMock()
    embeddings.embed_query.return_value = [0.1, 0.2]
    openai_embeddings = MagicMock(return_value=embeddings)
    db = MagicMock()
    db.fetch_one.return_value = None
    monkeypatch.setattr(main, "Database", lambda **kwargs: db)
    monkeypatch.setattr(main, "apply_schema", lambda db: None)
    monkeypatch.setattr(main, "OpenAIEmbeddings", openai_embeddings)
    monkeypatch.setattr(main.settings, "EMBEDDING_MODEL", "text-embedding-3-small")

    graph = main.create_app(service_config=ServiceConfig(), service_factories=factories, model_config=config)
    clients = graph.model_registry.models
    assert len(created) == 6
    assert graph.nodes.analysis.analyzer.llm is clients["message_analyzer"]
    assert graph.nodes.guard.validator.llm is clients["request_validator"]
    assert graph.nodes.chat.llm is clients["chat"]
    assert graph.nodes.recipe.recipe_generation_service.llm is clients["recipe_generation"]
    assert graph.nodes.response.llm is clients["response"]

    async def run_requests():
        a, b = await asyncio.gather(graph.arun(ChatGraphRequest("Hi!", "A", dietitian_id="clinic_001")), graph.arun(ChatGraphRequest("Hello!", "B", dietitian_id="clinic_001")))
        for result in (a, b):
            assert result["result"]["text"] == "Reply from chat"
            assert result["final_response"] == "Reply from response"
        result = await graph.arun(ChatGraphRequest("invent an original recipe", "C", dietitian_id="clinic_001"))
        assert result["result"]["text"] == "Reply from recipe_generation"
        assert result["final_response"] == "Reply from response"

    asyncio.run(run_requests())
    assert len(created) == 6  # no construction per request
    assert clients["message_analyzer"].calls == ["MessageAnalysis"] * 3
    assert clients["request_validator"].calls == ["RequestValidation"] * 3
    assert clients["chat"].calls == ["text"] * 2
    assert clients["recipe_generation"].calls == ["text"]
    assert clients["response"].calls == ["text"] * 3

    ingestion = main.create_ingest_recipe(service_config=ServiceConfig(), service_factories=factories, model_config=config)
    assert ingestion.run(RecipeIngestionRequest("Some recipe text", dietitian_id="clinic_001"))["success"] is True
    assert ingestion.model_registry.models["recipe_extractor"].calls == ["RecipeCreate"]
    assert ingestion.embedding_node.embedding_model is embeddings
    assert ingestion.persist_node.embedding_model_name == "text-embedding-3-small"
    assert all(call.kwargs == {"model": "text-embedding-3-small"} for call in openai_embeddings.call_args_list)
