from Graph.GraphState import GraphState
from Models.RecipePlan import RecipePlan
from Services.RecipeGenerationService import RecipeGenerationService
from Services.RecipeSearchService import RecipeSearchService


class RecipeNode:

    def __init__(
        self,
        recipe_generation_service: RecipeGenerationService,
        recipe_search_service: RecipeSearchService,
    ):
        self.recipe_generation_service = recipe_generation_service
        self.recipe_search_service = recipe_search_service

    def __call__(self, state: GraphState) -> dict:
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
            recipes = self.recipe_search_service.find_best_recipes(
                user_text=state["message"],
                plan=plan,
            )
            if not recipes:
                return {
                    "result": {
                        "mode": "not_found",
                        "recipes": [],
                        "message": (
                            "No matching Alona recipe was found in the database "
                            "for this request. Do not invent a recipe."
                        ),
                    }
                }
            return {
                "result": {
                    "mode": "existing",
                    "recipes": [recipe.model_dump(mode="json") for recipe in recipes],
                }
            }

        if mode == "inspired":
            similar_recipes = self.recipe_search_service.find_best_recipes(
                user_text=state["message"],
                plan=plan,
            )
            text = self.recipe_generation_service.generate_inspired_recipe(
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
            text = self.recipe_generation_service.generate_original_recipe(plan=plan)
            return {
                "result": {
                    "mode": "original",
                    "text": text,
                }
            }

        return {"result": None}
