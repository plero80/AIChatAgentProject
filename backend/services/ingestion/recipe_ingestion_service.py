import re
from uuid import UUID, uuid4

from backend.paths import DATA_DIR
from backend.models.recipe_create import RecipeCreate
from backend.services.contracts import BaseRecipeIngestionService
from backend.services.exceptions import RecipeAlreadyExists

_IMAGES = DATA_DIR / "recipe_images"


class RecipeIngestionService(BaseRecipeIngestionService):

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
