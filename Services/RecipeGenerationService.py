from langchain_core.language_models.chat_models import BaseChatModel
from Models.RecipePlan import RecipePlan
from Models.Recipe import Recipe


class RecipeGenerationService:

    def __init__(
        self,
        llm: BaseChatModel
    ):
        self.llm = llm

    def generate_original_recipe(
        self,
        plan: RecipePlan
    ) -> str:

        response = self.llm.invoke(
            f"""
            Create an original recipe.

            Ingredients:
            {plan.ingredients}

            Preferences:
            {plan.preferences}
            """
        )

        return response.content

    
    def generate_inspired_recipe(
        self,
        plan: RecipePlan,
        similar_recipes: list[Recipe],
    ) -> str:

        response = self.llm.invoke(
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

        return response.content