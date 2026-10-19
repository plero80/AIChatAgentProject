from langchain_core.language_models.chat_models import BaseChatModel
from backend.models.recipe_plan import RecipePlan
from backend.models.recipe import Recipe
from backend.services.contracts import BaseRecipeGenerationService


class RecipeGenerationService(BaseRecipeGenerationService):

    def __init__(
        self,
        llm: BaseChatModel
    ):
        self.llm = llm

    async def generate_original_recipe(
        self,
        plan: RecipePlan
    ) -> str:

        response = await self.llm.ainvoke(
            f"""
            Invent a fully ORIGINAL recipe of your own.
            This is NOT an Alona Eckrling recipe and must not come from any
            saved recipe database. Do not reuse a known Alona recipe name
            or Instagram URL. Clearly present it as your original idea.

            Match the user's language (Hebrew if they wrote in Hebrew).

            Ingredients to use / emphasize:
            {plan.ingredients}

            Preferences:
            {plan.preferences}
            """
        )

        return str(response.text)

    async def generate_inspired_recipe(
        self,
        plan: RecipePlan,
        similar_recipes: list[Recipe],
    ) -> str:

        response = await self.llm.ainvoke(
            f"""
            Create a new recipe inspired by the recipes below.

            User ingredients:
            {plan.ingredients}

            User preferences:
            {plan.preferences}

            Reference recipes:
            {similar_recipes}

            Do not present the result as an existing Alona recipe.
            """
        )

        return str(response.text)
