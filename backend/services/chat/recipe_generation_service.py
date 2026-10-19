from backend.tenancy import TenantContext, load_tenant

from langchain_core.language_models.chat_models import BaseChatModel
from backend.models.recipe_plan import RecipePlan
from backend.models.recipe import Recipe
from backend.services.contracts import BaseRecipeGenerationService


class RecipeGenerationService(BaseRecipeGenerationService):

    def __init__(
        self,
        llm: BaseChatModel,
        *, tenant: TenantContext | None = None,
    ):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.llm = llm

    async def generate_original_recipe(
        self,
        plan: RecipePlan
    ) -> str:

        response = await self.llm.ainvoke(
            self.tenant.render("generate_original", ingredients=plan.ingredients, preferences=plan.preferences)
        )

        return str(response.text)

    async def generate_inspired_recipe(
        self,
        plan: RecipePlan,
        similar_recipes: list[Recipe],
    ) -> str:

        response = await self.llm.ainvoke(
            self.tenant.render("generate_inspired", ingredients=plan.ingredients, preferences=plan.preferences, reference_recipes=similar_recipes)
        )

        return str(response.text)
