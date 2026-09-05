from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class Ingredient(BaseModel):
    id: UUID
    recipe_id: UUID

    ingredient_name: str

    quantity: Decimal | None = None
    unit: str | None = None


class Recipe(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    instructions: str

    instagram_url: str | None = None
    image_url: str | None = None
    video_url: str | None = None

    ingredients: list[Ingredient] = []

    created_at: datetime


class RecipeEmbedding(BaseModel):
    recipe_id: UUID
    embedding: list[float]
    model: str
    created_at: datetime
