from Graph.GraphState import RecipeIngestionState
from Services.RecipeExtractorService import RecipeExtractorService


class ExtractRecipeNode:

    def __init__(
        self,
        extractor: RecipeExtractorService,
    ):
        self.extractor = extractor

    def __call__(
        self,
        state: RecipeIngestionState,
    ) -> dict:

        recipe = self.extractor.extract(
            recipe_text=state["raw_recipe"],
            instagram_url=state.get("instagram_url"),
        )

        return {
            "recipe": recipe
        }