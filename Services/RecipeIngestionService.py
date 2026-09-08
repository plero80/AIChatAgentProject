from uuid import UUID, uuid4

from Models.RecipeCreate import RecipeCreate


class RecipeAlreadyExists(Exception):
    def __init__(self, recipe_id: UUID, name: str):
        super().__init__(f"Recipe already ingested: {name} ({recipe_id})")
        self.recipe_id = recipe_id
        self.name = name


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

        existing_id = self.recipe_repository.find_existing_id(
            name=recipe.name,
            instagram_url=recipe.instagram_url,
        )

        if existing_id is not None:
            raise RecipeAlreadyExists(existing_id, recipe.name)

        # Create ONE ID for this recipe
        recipe_id = uuid4()

        with self.recipe_repository.db.transaction() as tx:
            self.recipe_repository.create(
                recipe_id=recipe_id,
                recipe=recipe,
                db=tx,
            )
            self.ingredient_repository.create_many(
                recipe_id=recipe_id,
                ingredients=recipe.ingredients,
                db=tx,
            )
            self.embedding_repository.create(
                recipe_id=recipe_id,
                embedding=embedding,
                model=embedding_model,
                db=tx,
            )

        return recipe_id
