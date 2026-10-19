"""Build a container of chat nodes, one configuration step at a time."""

from backend.tenancy import TenantContext, load_tenant

from typing import Self

from langchain_core.language_models.chat_models import BaseChatModel

from backend.graphs.recipe_chat_nodes import RecipeChatNodes
from backend.nodes.chat.analyze_node import AnalyzeNode
from backend.nodes.chat.chat_node import ChatNode
from backend.nodes.chat.dietitian_node import DietitianNode
from backend.nodes.chat.guard_node import GuardNode
from backend.nodes.chat.recipe_node import RecipeNode
from backend.nodes.chat.response_node import ResponseNode
from backend.services.contracts import (
    BaseDietitianService,
    BaseMessageAnalyzer,
    BaseRecipeGenerationService,
    BaseRecipePresentationService,
    BaseRecipeSearchService,
    BaseRequestValidator,
)


class RecipeChatBuilder:
    def __init__(self, *, tenant: TenantContext | None = None):
        self.tenant = tenant if tenant is not None else load_tenant()
        self._analysis: AnalyzeNode | None = None
        self._recipe: RecipeNode | None = None
        self._chat: ChatNode | None = None
        self._response: ResponseNode | None = None
        self._dietitian: DietitianNode | None = None
        self._validator: BaseRequestValidator | None = None

    def analysisNode(self, analyzer: BaseMessageAnalyzer) -> Self:
        self._analysis = AnalyzeNode(analyzer=analyzer)
        return self

    def recipeNode(
        self,
        recipe_generation_service: BaseRecipeGenerationService,
        recipe_search_service: BaseRecipeSearchService,
    ) -> Self:
        self._recipe = RecipeNode(
            recipe_generation_service=recipe_generation_service,
            recipe_search_service=recipe_search_service,
            tenant=self.tenant,
        )
        return self

    def chatNode(self, llm: BaseChatModel) -> Self:
        self._chat = ChatNode(llm=llm, tenant=self.tenant)
        return self

    def responseNode(
        self, llm: BaseChatModel, presentation_service: BaseRecipePresentationService,
    ) -> Self:
        self._response = ResponseNode(llm=llm, presentation_service=presentation_service, tenant=self.tenant)
        return self

    def dietitianNode(self, dietitian_service: BaseDietitianService) -> Self:
        self._dietitian = DietitianNode(dietitian_service=dietitian_service, tenant=self.tenant)
        return self

    def guardNode(self, validator: BaseRequestValidator) -> Self:
        # Wire the guard to the final branch nodes in build(), regardless of
        # which order the caller configures those nodes.
        self._validator = validator
        return self

    def build(self) -> RecipeChatNodes:
        """Return the configured nodes. The graph owns edges and compilation."""
        if (
            self._analysis is None
            or self._recipe is None
            or self._chat is None
            or self._response is None
            or self._dietitian is None
            or self._validator is None
        ):
            missing = [name for name, value in (
                ("analysisNode", self._analysis),
                ("recipeNode", self._recipe),
                ("chatNode", self._chat),
                ("responseNode", self._response),
                ("dietitianNode", self._dietitian),
                ("guardNode", self._validator),
            ) if value is None]
            raise ValueError("Configure these nodes before build(): " + ", ".join(missing))

        guard = GuardNode(
            validator=self._validator,
            chat_node=self._chat,
            recipe_node=self._recipe,
            dietitian_node=self._dietitian,
            tenant=self.tenant,
        )
        return RecipeChatNodes(
            analysis=self._analysis,
            recipe=self._recipe,
            chat=self._chat,
            response=self._response,
            dietitian=self._dietitian,
            guard=guard,
        )
