from backend.models.recipe_create import RecipeCreate
from backend.services.ingestion.recipe_ingestion_service import _attach_saved_image


def test_ingestion_attaches_a_bundled_image_after_project_reorganization():
    recipe = RecipeCreate(
        name="Recipe with a saved Instagram photo",
        description="Test",
        ingredients=[],
        instagram_url="https://www.instagram.com/p/Db3SxwotrQg/",
    )

    _attach_saved_image(recipe)

    assert recipe.image_url == "/recipe-images/Db3SxwotrQg.jpg"
