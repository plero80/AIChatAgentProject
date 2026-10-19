"""Service contracts consumed by nodes and implemented by replaceable services."""

from abc import ABC, abstractmethod
from uuid import UUID

from backend.models.chat_response import ChatResponse
from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe import Recipe
from backend.models.recipe_create import RecipeCreate
from backend.models.recipe_plan import RecipePlan
from backend.models.request_validation import RequestValidation


class BaseMessageAnalyzer(ABC):
    @abstractmethod
    async def analyze(
        self, message: str, current_user_message: str | None = None,
    ) -> MessageAnalysis:
        """Classify the message; current_user_message excludes conversation history."""


class BaseRequestValidator(ABC):
    @abstractmethod
    async def validate(self, message: str, history: str = "") -> RequestValidation:
        """Decide whether the request is legitimate."""


class BaseRecipeSearchService(ABC):
    @abstractmethod
    async def find_best_recipes(
        self, user_text: str, plan: RecipePlan, top_k: int = 5,
    ) -> list[Recipe]:
        """Return matching saved recipes in relevance order."""


class BaseRecipeGenerationService(ABC):
    @abstractmethod
    async def generate_original_recipe(self, plan: RecipePlan) -> str:
        """Generate an original recipe."""

    @abstractmethod
    async def generate_inspired_recipe(
        self, plan: RecipePlan, similar_recipes: list[Recipe],
    ) -> str:
        """Generate a new recipe inspired by the supplied recipes."""


class BaseRecipeExtractorService(ABC):
    @abstractmethod
    def extract(
        self, recipe_text: str, instagram_url: str | None = None,
    ) -> RecipeCreate:
        """Extract structured recipe data from source text."""


class BaseRecipeIngestionService(ABC):
    @abstractmethod
    def save(
        self, recipe: RecipeCreate, embedding: list[float], embedding_model: str,
    ) -> UUID:
        """Persist a recipe atomically, or raise RecipeAlreadyExists."""


class BaseDietitianService(ABC):
    @abstractmethod
    def get_dietitian_url(self) -> str:
        """Return the dietitian form URL."""


class BaseRecipePresentationService(ABC):
    @abstractmethod
    def build_text_response(self, text: str) -> ChatResponse:
        """Present a text response."""

    @abstractmethod
    def build_recipe_response(
        self, workflow_result: dict, recipe_ids: list[str],
        intro: str = "", outro: str = "",
    ) -> ChatResponse:
        """Present only selected saved recipes; reject unknown recipe IDs."""
