# Nodes/Ingestion/PersistRecipeNode.py

from Graph.GraphState import RecipeIngestionState
from Services.RecipeIngestionService import (
    RecipeAlreadyExists,
    RecipeIngestionService,
)


class PersistRecipeNode:

    def __init__(
        self,
        ingestion_service: RecipeIngestionService,
        embedding_model_name: str,
    ):
        self.ingestion_service = ingestion_service
        self.embedding_model_name = embedding_model_name

    def __call__(
        self,
        state: RecipeIngestionState,
    ) -> dict:

        try:
            recipe_id = self.ingestion_service.save(
                recipe=state["recipe"],
                embedding=state["embedding"],
                embedding_model=self.embedding_model_name,
            )
        except RecipeAlreadyExists as exc:
            return {
                "recipe_id": exc.recipe_id,
                "success": False,
                "error": str(exc),
            }

        return {
            "recipe_id": recipe_id,
            "success": True,
        }