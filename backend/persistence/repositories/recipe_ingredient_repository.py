from backend.tenancy import validate_dietitian_id

from backend.models.recipe import Ingredient
from backend.persistence.database import Database
from uuid import UUID
from backend.models.recipe_create import IngredientCreate
from uuid import uuid4


class RecipeIngredientRepository:

    def __init__(self, db: Database, dietitian_id: str):
        self.db = db
        self.dietitian_id = validate_dietitian_id(dietitian_id)

    def find_recipe_with_ingredients(
        self,
        ingredients: list[str],
    ) -> list[UUID]:
        """
        Match when a stored ingredient name CONTAINS the search term
        (e.g. "אורז" matches "אורז בסמטי / יסמין").
        Still same-language substring match — not bilingual aliases.
        """
        terms = [t.strip().lower() for t in ingredients if t and t.strip()]
        if not terms:
            return []

        rows = self.db.fetch_all(
            """
            SELECT
                ri.recipe_id,
                COUNT(DISTINCT term) AS match_count
            FROM recipe_ingredients AS ri
            CROSS JOIN UNNEST(%s::text[]) AS term
            WHERE ri.dietitian_id = %s AND LOWER(ri.ingredient_name) LIKE ('%%' || term || '%%')
            GROUP BY ri.recipe_id
            ORDER BY match_count DESC
            """,
            (terms, self.dietitian_id),
        )

        return [row["recipe_id"] for row in rows]

    def get_ingredients(
        self,
        recipe_id: UUID,
    ) -> list[Ingredient]:

        rows = self.db.fetch_all(
            """
            SELECT
                id,
                recipe_id,
                ingredient_name,
                quantity,
                unit
            FROM recipe_ingredients
            WHERE dietitian_id = %s AND recipe_id = %s
            ORDER BY id
            """,
            (self.dietitian_id, recipe_id),
        )

        return [Ingredient(**row) for row in rows]

    def create_many(
        self,
        recipe_id: UUID,
        ingredients: list[IngredientCreate],
        db=None,
    ) -> None:

        conn = db or self.db
        for ingredient in ingredients:
            ingredient_id = uuid4()

            conn.execute(
                """
                INSERT INTO recipe_ingredients (
                    dietitian_id,
                    id,
                    recipe_id,
                    ingredient_name,
                    quantity,
                    unit
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    self.dietitian_id,
                    ingredient_id,
                    recipe_id,
                    ingredient.ingredient_name,
                    ingredient.quantity,
                    ingredient.unit,
                ),
            )

