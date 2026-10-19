import asyncio

from backend.persistence.repositories.recipe_repository import RecipeRepository
from backend.persistence.repositories.recipe_ingredient_repository import RecipeIngredientRepository
from backend.persistence.repositories.recipe_embedding_repository import RecipeEmbeddingRepository

from backend.models.recipe import Recipe
from backend.models.recipe_plan import RecipePlan
from backend.services.contracts import BaseRecipeSearchService

from langchain_core.embeddings import Embeddings
from uuid import UUID


class RecipeSearchService(BaseRecipeSearchService):

    def __init__(
        self,
        recipe_repository: RecipeRepository,
        ingredient_repository: RecipeIngredientRepository,
        embedding_repository: RecipeEmbeddingRepository,
        embedding_model: Embeddings,
    ):
        self.recipe_repository = recipe_repository
        self.ingredient_repository = ingredient_repository
        self.embedding_repository = embedding_repository
        self.embedding_model = embedding_model

    def find_candidate_recipe_ids(
        self,
        ingredients: list[str],
    ) -> list[UUID]:
        return self.ingredient_repository.find_recipe_with_ingredients(ingredients)

    async def find_best_recipes(
        self,
        user_text: str,
        plan: RecipePlan,
        top_k: int = 5,
    ) -> list[Recipe]:
        # psycopg is blocking, so every DB call runs off the event loop
        candidate_ids, query_embedding = await asyncio.gather(
            asyncio.to_thread(self.find_candidate_recipe_ids, plan.ingredients),
            self.embedding_model.aembed_query(user_text),
        )

        # If no ingredient hits, still rank by semantic similarity across all recipes
        ranked = await asyncio.to_thread(
            self.embedding_repository.find_most_similar_recipes,
            query_embedding,
            candidate_ids or None,
            top_k,
        )
        recipe_ids = [recipe_id for recipe_id, _distance in ranked]
        if not recipe_ids:
            return []

        return await asyncio.to_thread(self._load_recipes, recipe_ids)

    def _load_recipes(self, recipe_ids: list[UUID]) -> list[Recipe]:
        recipes = self.recipe_repository.get_many(recipe_ids)
        return [
            recipe.model_copy(
                update={
                    "ingredients": self.ingredient_repository.get_ingredients(recipe.id),
                }
            )
            for recipe in recipes
        ]
