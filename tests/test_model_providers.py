import asyncio
from unittest.mock import MagicMock, call

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

import backend.bootstrap as main
from backend.graphs.graph_requests import ChatGraphRequest, RecipeIngestionRequest
from backend.factories.service_factories import ServiceFactories
from backend.config.models import ModelProfile, load_model_config
from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe_create import RecipeCreate
from backend.models.recipe_plan import RecipePlan
from backend.models.request_validation import RequestValidation
from backend.config.services import ServiceConfig, ServiceConfigurationError, load_service_config
from backend.services.chat.recipe_generation_service import RecipeGenerationService


@pytest.fixture
def dummy_provider_keys(monkeypatch):
    # Constructors and schema binding only: these tests never invoke a live model.
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(name, "test-key-not-a-real-credential")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")


@pytest.mark.parametrize(("provider", "model_name"), [
    ("openai", "gpt-5.4-mini"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("google_genai", "gemini-2.5-flash"),
])
def test_chat_adapters_construct_and_bind_structured_output(dummy_provider_keys, provider, model_name):
    profile = ModelProfile(
        provider=provider, model=model_name, max_tokens=256,
        reasoning_effort="none" if provider == "openai" else None,
    )
    model = ServiceFactories().chat_model.create(provider, profile=profile)
    assert isinstance(model, BaseChatModel)
    assert callable(model.with_structured_output(RequestValidation).ainvoke)
    assert callable(model.with_structured_output(RecipeCreate, method="function_calling").invoke)
    if provider == "openai":
        assert model.reasoning_effort == "none"
    else:
        # OpenAI options must not become unsupported API arguments on other providers.
        assert model.reasoning_effort is None
        assert "reasoning_effort" not in model.model_kwargs


def test_openai_reasoning_option_can_be_omitted(dummy_provider_keys):
    profile = ModelProfile(provider="openai", model="gpt-4.1-mini", max_tokens=256)
    model = ServiceFactories().chat_model.create("openai", profile=profile)
    assert model.reasoning_effort is None


def test_profiles_of_one_provider_have_independent_models_and_settings(dummy_provider_keys):
    factory = ServiceFactories().chat_model
    classifier = factory.create("openai", profile=ModelProfile(
        provider="openai", model="gpt-4.1-mini", max_tokens=256,
    ))
    writer = factory.create("openai", profile=ModelProfile(
        provider="openai", model="gpt-5.4-mini", max_tokens=4000, reasoning_effort="none",
    ))
    assert classifier.model_name == "gpt-4.1-mini"
    assert classifier.max_tokens == 256
    assert classifier.reasoning_effort is None
    assert writer.model_name == "gpt-5.4-mini"
    assert writer.max_tokens == 4000
    assert writer.reasoning_effort == "none"


def test_provider_choices_use_existing_configuration_precedence(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text('{"chat_model":"anthropic"}', encoding="utf-8")
    config = load_service_config(path, environ={"CHAT_MODEL_TYPE": "google_genai"})
    assert config.chat_model == "google_genai"
    ServiceFactories().validate(config)
    with pytest.raises(ServiceConfigurationError, match="Unknown chat_model"):
        ServiceFactories().validate(ServiceConfig(chat_model="unknown"))


class AlternativeChatModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "alternative-test-provider"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=[
            {"type": "thinking", "thinking": "Internal metadata"},
            {"type": "text", "text": "Alternative provider reply"},
        ]))])

    def with_structured_output(self, schema, **kwargs):
        outputs = {
            MessageAnalysis: MessageAnalysis(intent="chat", recipe_mode="none"),
            RequestValidation: RequestValidation(legitimate=True),
            RecipeCreate: RecipeCreate(name="Provider test recipe", description="Test", ingredients=[]),
        }
        return RunnableLambda(lambda messages: outputs[schema])


def test_chat_provider_switch_keeps_openai_embeddings_for_chat_and_ingestion(monkeypatch, tmp_path):
    llm = AlternativeChatModel()
    embeddings = MagicMock()
    embeddings.embed_query.return_value = [0.1, 0.2]
    openai_embeddings = MagicMock(return_value=embeddings)
    monkeypatch.setattr(main, "OpenAIEmbeddings", openai_embeddings)
    factories = ServiceFactories()
    factories.chat_model.register("alternative", lambda profile: llm)
    config = ServiceConfig(chat_model="alternative")
    # Only the fake provider participates, regardless of local profiles or env overrides.
    path = tmp_path / "models.json"
    path.write_text("{}", encoding="utf-8")
    model_config = load_model_config(path, service_config=config, environ={})
    db = MagicMock()
    db.fetch_one.return_value = None
    monkeypatch.setattr(main, "Database", lambda **kwargs: db)
    monkeypatch.setattr(main, "apply_schema", lambda db: None)
    monkeypatch.setattr(main.settings, "EMBEDDING_MODEL", "text-embedding-3-small")

    chat = main.create_app(service_config=config, service_factories=factories, model_config=model_config)
    assert chat.nodes.analysis.analyzer.llm is llm
    result = asyncio.run(chat.arun(ChatGraphRequest("hello", "provider-test")))
    assert result["final_response"] == "Alternative provider reply"
    assert result["result"]["text"] == "Alternative provider reply"
    assert "Internal metadata" not in str(result)

    ingestion = main.create_ingest_recipe(service_config=config, service_factories=factories, model_config=model_config)
    assert ingestion.embedding_node.embedding_model is embeddings
    assert ingestion.persist_node.embedding_model_name == "text-embedding-3-small"
    assert ingestion.run(RecipeIngestionRequest("Some recipe text"))["success"] is True
    embeddings.embed_query.assert_called_once()
    transaction = db.transaction.return_value.__enter__.return_value
    assert transaction.execute.call_args.args[1][2] == "text-embedding-3-small"
    assert openai_embeddings.call_args_list == [
        call(model="text-embedding-3-small"),
        call(model="text-embedding-3-small"),
    ]


def test_generation_extracts_text_from_provider_content_blocks():
    generation = RecipeGenerationService(AlternativeChatModel())
    plan = RecipePlan(ingredients=[], preferences=[], mode="original")
    assert asyncio.run(generation.generate_original_recipe(plan)) == "Alternative provider reply"
    assert asyncio.run(generation.generate_inspired_recipe(plan, [])) == "Alternative provider reply"
