# Nodes/Ingestion/PrepareEmbeddingNode.py

from Graph.GraphState import RecipeIngestionState


class PrepareEmbeddingNode:

    def __call__(
        self,
        state: RecipeIngestionState,
    ) -> dict:

        recipe = state["recipe"]

        ingredients = ", ".join(
            ingredient.ingredient_name
            for ingredient in recipe.ingredients
        )

        embedding_text = f"""
        Recipe name:
        {recipe.name}

        Description:
        {recipe.description or ""}

        Ingredients:
        {ingredients}

        Instructions:
        {recipe.instructions}
        """

        return {
            "embedding_text": embedding_text
        }