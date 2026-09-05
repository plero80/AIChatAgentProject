from uuid import UUID, uuid4

from Models.RecipeCreate import RecipeCreate


class RecipeIngestionService:

    def __init__(
        self,
        recipe_repository,
        ingredient_repository,
        embedding_repository,
    ):
        self.recipe_repository = recipe_repository
        self.ingredient_repository = ingredient_repository
        self.embedding_repository = embedding_repository

    def save(
        self,
        recipe: RecipeCreate,
        embedding: list[float],
        embedding_model: str,
    ) -> UUID:

        # Create ONE ID for this recipe
        recipe_id = uuid4()

        # recipes
        self.recipe_repository.create(
            recipe_id=recipe_id,
            recipe=recipe,
        )

        # recipe_ingredients
        self.ingredient_repository.create_many(
            recipe_id=recipe_id,
            ingredients=recipe.ingredients,
        )

        # recipe_embeddings
        self.embedding_repository.create(
            recipe_id=recipe_id,
            embedding=embedding,
            model=embedding_model,
        )

        return recipe_id