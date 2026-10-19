from backend.graphs.graph_state import RecipeIngestionState
from backend.services.contracts import BaseRecipeExtractorService


class ExtractRecipeNode:

    def __init__(
        self,
        extractor: BaseRecipeExtractorService,
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
