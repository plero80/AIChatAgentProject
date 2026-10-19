from pydantic import BaseModel, Field


class RecipePresentationPlan(BaseModel):
    """Choose saved recipes without regenerating their content or image URLs."""

    intro: str = Field(description="Brief answer before the recipe cards, in the user's language.")
    recipe_ids: list[str] = Field(
        description=(
            "Exact IDs of the supplied saved recipes to display, in display order. "
            "Only include recipes actually being presented, not alternatives mentioned "
            "in passing. Empty when the answer needs no complete recipe card."
        )
    )
    outro: str = Field(description="Optional follow-up text after the cards; empty if unnecessary.")
