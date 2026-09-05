from pydantic import BaseModel


class IngredientCreate(BaseModel):
    ingredient_name: str
    quantity: float | None = None
    unit: str | None = None


class RecipeCreate(BaseModel):
    name: str
    description: str | None = None
    instructions: str

    instagram_url: str | None = None

    ingredients: list[IngredientCreate]