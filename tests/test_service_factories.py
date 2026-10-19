import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

import backend.bootstrap as main
from backend.graphs.graph_requests import ChatGraphRequest, RecipeIngestionRequest
from backend.factories.service_factories import ServiceFactories
from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe_create import RecipeCreate
from backend.models.request_validation import RequestValidation
from backend.config.services import ServiceConfig, ServiceConfigurationError
from backend.services.contracts import (
    BaseDietitianService,
    BaseMessageAnalyzer,
    BaseRecipeExtractorService,
    BaseRecipeGenerationService,
    BaseRecipeIngestionService,
    BaseRecipeSearchService,
    BaseRequestValidator,
)
from backend.services.chat.recipe_presentation_service import RecipePresentationService


class ExperimentalAnalyzer(BaseMessageAnalyzer):
    def __init__(self):
        self.calls = []

    async def analyze(self, message, current_user_message=None):
        self.calls.append((message, current_user_message))
        return MessageAnalysis(
            intent="dietitian_escort" if current_user_message == "support" else "food",
            recipe_mode="original" if current_user_message == "invent" else "existing",
        )


def test_every_factory_requires_a_complete_abstract_contract():
    factories = ServiceFactories()
    for name in ServiceConfig.model_fields:
        factory = getattr(factories, name)
        with pytest.raises(TypeError):
            factory.contract()
        with pytest.raises(TypeError, match="must implement"):
            factory.register("incomplete", factory.contract)
        with pytest.raises(TypeError, match="must implement"):
            factory.register("unrelated", object)


def test_registration_is_isolated_and_does_not_replace_an_existing_type():
    first, second = ServiceFactories(), ServiceFactories()
    first.message_analyzer.register("experimental", lambda llm: ExperimentalAnalyzer())
    with pytest.raises(ValueError, match="already registered"):
        first.message_analyzer.register("llm", ExperimentalAnalyzer)
    with pytest.raises(ServiceConfigurationError):
        second.message_analyzer.create("experimental", llm=object())
    first.message_analyzer.register("broken", lambda llm: object())
    with pytest.raises(TypeError, match="must return BaseMessageAnalyzer"):
        first.message_analyzer.create("broken", llm=object())


@pytest.mark.parametrize("builder", [main.create_app, main.create_ingest_recipe])
def test_bad_config_fails_before_models_or_database_are_created(monkeypatch, builder):
    dependencies = MagicMock(side_effect=AssertionError("External dependency created"))
    monkeypatch.setattr(main, "get_required_services", dependencies)
    with pytest.raises(ServiceConfigurationError):
        builder(service_config=ServiceConfig(message_analyzer="missing"))
    dependencies.assert_not_called()


def test_default_factories_build_chat_and_ingestion_without_network(monkeypatch):
    llm = MagicMock()
    models = SimpleNamespace(for_role=lambda role: llm)
    monkeypatch.setattr(main, "get_required_services", lambda config, factories, model_config: (models, object(), object(), object(), object()))
    config = ServiceConfig()
    chat = main.create_app(service_config=config)
    ingestion = main.create_ingest_recipe(service_config=config)
    assert isinstance(chat.nodes.analysis.analyzer, BaseMessageAnalyzer)
    assert isinstance(chat.nodes.guard.validator, BaseRequestValidator)
    assert isinstance(ingestion.extract_node.extractor, BaseRecipeExtractorService)
    assert isinstance(ingestion.persist_node.ingestion_service, BaseRecipeIngestionService)


def test_alternative_services_run_through_the_existing_chat_graph(monkeypatch, tmp_path):
    analyzer = ExperimentalAnalyzer()
    calls = []

    class Validator(BaseRequestValidator):
        async def validate(self, message, history=""):
            calls.append("validate")
            return RequestValidation(legitimate=True)

    class Search(BaseRecipeSearchService):
        async def find_best_recipes(self, user_text, plan, top_k=5):
            calls.append("search")
            return []

    class Generation(BaseRecipeGenerationService):
        async def generate_original_recipe(self, plan):
            calls.append("generate")
            return "Experimental recipe"

        async def generate_inspired_recipe(self, plan, similar_recipes):
            return "Inspired recipe"

    class Dietitian(BaseDietitianService):
        def get_dietitian_url(self):
            calls.append("dietitian")
            return "https://example.com/form"

    class Presentation(RecipePresentationService):
        def build_text_response(self, text):
            calls.append("present")
            return super().build_text_response("Custom presentation: " + text)

    class LLM:
        async def ainvoke(self, messages):
            return AIMessage(content="Rendered answer")

    factories = ServiceFactories()
    factories.message_analyzer.register("experimental", lambda llm: analyzer)
    factories.request_validator.register("experimental", lambda llm: Validator())
    factories.recipe_search.register("experimental", lambda **deps: Search())
    factories.recipe_generation.register("experimental", lambda llm: Generation())
    factories.dietitian.register("experimental", Dietitian)
    factories.recipe_presentation.register("experimental", Presentation)
    models = SimpleNamespace(for_role=lambda role: LLM())
    monkeypatch.setattr(main, "get_required_services", lambda config, factories, model_config: (models, object(), object(), object(), object()))
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps({
        name: "experimental" for name in (
            "request_validator", "recipe_search", "recipe_generation",
            "dietitian", "recipe_presentation",
        )
    }), encoding="utf-8")
    monkeypatch.setenv("SERVICES_CONFIG", str(path))
    monkeypatch.setenv("MESSAGE_ANALYZER_TYPE", "experimental")
    graph = main.create_app(service_factories=factories)

    async def conversation():
        for message, mode in [("invent", "original"), ("find", "not_found"), ("support", "dietitian_escort")]:
            result = await graph.arun(ChatGraphRequest(message, "factory-test"))
            assert result["result"]["mode"] == mode
            assert result["final_response"] == "Custom presentation: Rendered answer"
        assert "Experimental recipe" in str((await graph.arun(ChatGraphRequest("invent", "another-user")))["result"])

    asyncio.run(conversation())
    assert {"validate", "search", "generate", "dietitian", "present"} <= set(calls)
    assert analyzer.calls[0][1] == "invent"
    assert "Recent conversation:" in analyzer.calls[1][0]


def test_alternative_ingestion_services_run_through_the_existing_graph(monkeypatch):
    recipe_id = uuid4()
    saved = []

    class Extractor(BaseRecipeExtractorService):
        def extract(self, recipe_text, instagram_url=None):
            return RecipeCreate(name=recipe_text, description="Extracted", ingredients=[])

    class Ingestion(BaseRecipeIngestionService):
        def save(self, recipe, embedding, embedding_model):
            saved.append((recipe, embedding, embedding_model))
            return recipe_id

    class Embeddings:
        def embed_query(self, text):
            return [0.1, 0.2]

    factories = ServiceFactories()
    factories.recipe_extractor.register("experimental", lambda llm: Extractor())
    factories.recipe_ingestion.register("experimental", lambda **deps: Ingestion())
    models = SimpleNamespace(for_role=lambda role: object())
    monkeypatch.setattr(main, "get_required_services", lambda config, factories, model_config: (models, Embeddings(), object(), object(), object()))
    monkeypatch.setattr(main.settings, "EMBEDDING_MODEL", "test-embedding-model")
    graph = main.create_ingest_recipe(
        service_config=ServiceConfig(recipe_extractor="experimental", recipe_ingestion="experimental"),
        service_factories=factories,
    )
    result = graph.run(RecipeIngestionRequest("Recipe from a different extractor"))
    assert result["success"] is True
    assert result["recipe_id"] == recipe_id
    assert saved[0][0].name == "Recipe from a different extractor"
    assert saved[0][1:] == ([0.1, 0.2], "test-embedding-model")


def test_api_does_not_swallow_invalid_service_config(monkeypatch):
    import backend.web.api as api
    pool = MagicMock(side_effect=AssertionError("Database pool opened"))
    monkeypatch.setattr(api, "AsyncConnectionPool", pool)
    monkeypatch.setattr(api, "load_service_config", lambda: ServiceConfig(message_analyzer="typo"))

    async def start():
        async with api.lifespan(api.app):
            pytest.fail("API accepted an invalid service configuration")

    with pytest.raises(ServiceConfigurationError):
        asyncio.run(start())
    pool.assert_not_called()
