from backend.tenancy import TenantContext, load_tenant

from backend.graphs.graph_state import GraphState
from backend.models.recipe_plan import RecipePlan
from backend.services.contracts import BaseRecipeGenerationService, BaseRecipeSearchService


class RecipeNode:

    def __init__(
        self,
        recipe_generation_service: BaseRecipeGenerationService,
        recipe_search_service: BaseRecipeSearchService,
        *, tenant: TenantContext | None = None,
    ):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.recipe_generation_service = recipe_generation_service
        self.recipe_search_service = recipe_search_service

    async def __call__(self, state: GraphState) -> dict:
        analysis = state.get("analysis")
        if analysis is None or analysis.intent != "food":
            return {"result": None}

        # "none" on a food intent still means look up existing recipes
        mode = analysis.recipe_mode if analysis.recipe_mode != "none" else "existing"
        plan = RecipePlan(
            ingredients=analysis.ingredients,
            preferences=analysis.preferences,
            mode=mode,
        )

        if mode == "existing":
            recipes = await self.recipe_search_service.find_best_recipes(
                user_text=state["message"],
                plan=plan,
            )
            if not recipes:
                return {
                    "result": {
                        "mode": "not_found",
                        "recipes": [],
                        "message": self.tenant.render("recipe_not_found"),
                    }
                }
            return {
                "result": {
                    "mode": "existing",
                    "recipes": [recipe.model_dump(mode="json") for recipe in recipes],
                }
            }

        if mode == "inspired":
            similar_recipes = await self.recipe_search_service.find_best_recipes(
                user_text=state["message"],
                plan=plan,
            )
            text = await self.recipe_generation_service.generate_inspired_recipe(
                plan=plan,
                similar_recipes=similar_recipes,
            )
            return {
                "result": {
                    "mode": "inspired",
                    "text": text,
                }
            }

        if mode == "original":
            text = await self.recipe_generation_service.generate_original_recipe(
                plan=plan
            )
            return {
                "result": {
                    "mode": "original",
                    "text": text,
                }
            }

        return {"result": None}
