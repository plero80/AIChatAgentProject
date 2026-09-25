from pydantic import BaseModel
from pydantic import Field

class IngredientCreate(BaseModel):
    ingredient_name: str = Field(description="The name of the ingredient.")
    quantity: float | None = Field(description="The quantity of the ingredient.",default=None)
    unit: str | None = Field(description="The unit of the ingredient.",default=None)


class RecipeCreate(BaseModel):
    name: str = Field(description="The name of the recipe.")
    description: str = Field(description="The description of the recipe.")
    instructions: str | None = Field(description="The instructions for the recipe.",default=None)

    instagram_url: str | None = Field(description="The URL of the Instagram post where the recipe was posted.",default=None)
    image_url: str | None = Field(description="A stable URL of the recipe photo.",default=None)

    ingredients: list[IngredientCreate]

    