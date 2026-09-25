import re
from pathlib import Path
from uuid import UUID, uuid4

from Models.RecipeCreate import RecipeCreate

_IMAGES = Path(__file__).resolve().parent.parent / "recipe_images"


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

        _attach_saved_image(recipe)

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


def _attach_saved_image(recipe: RecipeCreate) -> None:
    """Use the photo already downloaded for this Instagram post."""
    if recipe.image_url or not recipe.instagram_url:
        return

    match = re.search(r"/p/([^/?#]+)", recipe.instagram_url)
    if match is None:
        return

    code = match.group(1)
    if (_IMAGES / f"{code}.jpg").is_file():
        recipe.image_url = f"/recipe-images/{code}.jpg"
