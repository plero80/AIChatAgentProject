from backend.tenancy import validate_dietitian_id

from backend.models.recipe import Recipe
from backend.persistence.database import Database
from uuid import UUID


from backend.models.recipe_create import RecipeCreate


class RecipeRepository:

    def __init__(self, db: Database, dietitian_id: str):
        self.db = db
        self.dietitian_id = validate_dietitian_id(dietitian_id)

    def get_by_id(
        self,
        recipe_id: UUID,
    ) -> Recipe | None:

        row = self.db.fetch_one(
            """
            SELECT
                id,
                name,
                description,
                instructions,
                instagram_url,
                image_url,
                created_at
            FROM recipes
            WHERE dietitian_id = %s AND id = %s
            """,
            (self.dietitian_id, recipe_id),
        )

        if row is None:
            return None

        return Recipe(**row)

    def get_many(self, recipes_ids: list[UUID]) -> list[Recipe]:
        if not recipes_ids:
            return []

        rows = self.db.fetch_all(
            """
            SELECT
                id,
                name,
                description,
                instructions,
                instagram_url,
                image_url,
                created_at
            FROM recipes
            WHERE dietitian_id = %s AND id = ANY(%s)
            """,
            (self.dietitian_id, recipes_ids),
        )

        by_id = {row["id"]: Recipe(**row) for row in rows}
        # Preserve ranking order from similarity search
        return [by_id[recipe_id] for recipe_id in recipes_ids if recipe_id in by_id]

    def find_existing_id(
        self,
        name: str,
        instagram_url: str | None = None,
    ) -> UUID | None:
        """Identify an already-ingested recipe by its Instagram post or exact name."""
        if instagram_url:
            row = self.db.fetch_one(
                "SELECT id FROM recipes WHERE dietitian_id = %s AND instagram_url = %s",
                (self.dietitian_id, instagram_url),
            )
            if row is not None:
                return row["id"]

        row = self.db.fetch_one(
            "SELECT id FROM recipes WHERE dietitian_id = %s AND name = %s",
            (self.dietitian_id, name),
        )

        return row["id"] if row is not None else None

    def search_by_name(self, name: str) -> list[Recipe]:
        rows = self.db.fetch_all(
            """
            SELECT
                id,
                name,
                description,
                instructions,
                instagram_url,
                image_url,
                created_at
            FROM recipes
            WHERE dietitian_id = %s AND name ILIKE %s
            """,
            (self.dietitian_id, f"%{name}%"),
        )

        return [Recipe(**row) for row in rows]


    def create(
        self,
        recipe_id: UUID,
        recipe: RecipeCreate,
        db=None,
    ) -> None:

        conn = db or self.db
        conn.execute(
            """
            INSERT INTO recipes (
                dietitian_id,
                id,
                name,
                description,
                instructions,
                instagram_url,
                image_url
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self.dietitian_id,
                recipe_id,
                recipe.name,
                recipe.description,
                recipe.instructions,
                recipe.instagram_url,
                recipe.image_url,
            ),
        )
