"""Presentation contract shared by the chat API and its clients."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class RecipeIngredient(BaseModel):
    ingredient_name: str
    quantity: str | int | float | None = None
    unit: str | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def preserve_decimal(cls, value):
        # Recipe.model_dump(mode="json") already emits Decimal as a string.
        # Also preserve its precision when callers supply a Python model dump.
        return str(value) if isinstance(value, Decimal) else value


class PresentedRecipe(BaseModel):
    id: str
    name: str
    description: str | None = None
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    instructions: str
    instagram_url: str | None = None
    image_url: str | None = None


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class RecipeBlock(BaseModel):
    type: Literal["recipe"] = "recipe"
    recipe: PresentedRecipe


ChatBlock = Annotated[TextBlock | RecipeBlock, Field(discriminator="type")]


class ChatResponse(BaseModel):
    # Keep a complete Markdown reply for conversation history and older clients.
    reply: str
    blocks: list[ChatBlock] = Field(default_factory=list)
