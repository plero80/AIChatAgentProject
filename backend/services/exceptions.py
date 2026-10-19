"""Domain errors shared by service implementations and their callers."""

from uuid import UUID


class RecipeAlreadyExists(Exception):
    def __init__(self, recipe_id: UUID, name: str):
        super().__init__(f"Recipe already ingested: {name} ({recipe_id})")
        self.recipe_id = recipe_id
        self.name = name
