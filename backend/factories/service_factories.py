"""Register implementations here once; select them through startup configuration."""

from backend.factories.service_factory import ServiceFactory
from backend.factories.model_builders import (
    anthropic_chat, google_chat, openai_chat,
)
from langchain_core.language_models.chat_models import BaseChatModel
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.graphs.recipe_chat_graph import RecipeChatGraph
from backend.config.services import ServiceConfig
from backend.services.contracts import (
    BaseDietitianService,
    BaseMessageAnalyzer,
    BaseRecipeExtractorService,
    BaseRecipeGenerationService,
    BaseRecipeIngestionService,
    BaseRecipePresentationService,
    BaseRecipeSearchService,
    BaseRequestValidator,
)
from backend.services.chat.dietitian_service import DietitianService
from backend.services.chat.message_analyzer import MessageAnalyzer
from backend.services.ingestion.recipe_extractor_service import RecipeExtractorService
from backend.services.chat.recipe_generation_service import RecipeGenerationService
from backend.services.ingestion.recipe_ingestion_service import RecipeIngestionService
from backend.services.chat.recipe_presentation_service import RecipePresentationService
from backend.services.chat.recipe_search_service import RecipeSearchService
from backend.services.chat.request_validator import RequestValidator


class ServiceFactories:
    def __init__(self):
        self.chat_graph = ServiceFactory("chat_graph", BaseChatGraph)
        self.chat_graph.register("recipe", RecipeChatGraph)

        self.chat_model = ServiceFactory("chat_model", BaseChatModel)
        self.chat_model.register("openai", openai_chat)
        self.chat_model.register("anthropic", anthropic_chat)
        self.chat_model.register("google_genai", google_chat)

        self.message_analyzer = ServiceFactory("message_analyzer", BaseMessageAnalyzer)
        self.message_analyzer.register("llm", MessageAnalyzer)

        self.request_validator = ServiceFactory("request_validator", BaseRequestValidator)
        self.request_validator.register("llm", RequestValidator)

        self.recipe_search = ServiceFactory("recipe_search", BaseRecipeSearchService)
        self.recipe_search.register("postgres", RecipeSearchService)

        self.recipe_generation = ServiceFactory("recipe_generation", BaseRecipeGenerationService)
        self.recipe_generation.register("llm", RecipeGenerationService)

        self.recipe_extractor = ServiceFactory("recipe_extractor", BaseRecipeExtractorService)
        self.recipe_extractor.register("llm", RecipeExtractorService)

        self.recipe_ingestion = ServiceFactory("recipe_ingestion", BaseRecipeIngestionService)
        self.recipe_ingestion.register("postgres", RecipeIngestionService)

        self.dietitian = ServiceFactory("dietitian", BaseDietitianService)
        self.dietitian.register("file", DietitianService)

        self.recipe_presentation = ServiceFactory("recipe_presentation", BaseRecipePresentationService)
        self.recipe_presentation.register("structured", RecipePresentationService)

    def validate(self, config: ServiceConfig) -> None:
        """Validate every choice before connecting to external dependencies."""
        for name, implementation in config.model_dump().items():
            getattr(self, name).validate(implementation)

    def available(self) -> dict[str, tuple[str, ...]]:
        return {name: getattr(self, name).available for name in ServiceConfig.model_fields}
