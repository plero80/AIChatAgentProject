from Models.Recipe import Recipe
from Database.Database import Database  
from uuid import UUID


from Models.RecipeCreate import RecipeCreate



class RecipeRepository:

    def __init__(self, db: Database):
        self.db = db

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
                created_at
            FROM recipes
            WHERE id = %s
            """,
            (recipe_id,),
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
                created_at
            FROM recipes
            WHERE id = ANY(%s)
            """,
            (recipes_ids,),
        )

        by_id = {row["id"]: Recipe(**row) for row in rows}
        # Preserve ranking order from similarity search
        return [by_id[recipe_id] for recipe_id in recipes_ids if recipe_id in by_id]

    def search_by_name(self, name: str) -> list[Recipe]:
        rows = self.db.fetch_all(
            """
            SELECT
                id,
                name,
                description,
                instructions,
                instagram_url,
                created_at
            FROM recipes
            WHERE name ILIKE %s
            """,
            (f"%{name}%",),
        )

        return [Recipe(**row) for row in rows]


    def create(
        self,
        recipe_id: UUID,
        recipe: RecipeCreate,
    ) -> None:

        self.db.execute(
            """
            INSERT INTO recipes (
                id,
                name,
                description,
                instructions,
                instagram_url
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                recipe_id,
                recipe.name,
                recipe.description,
                recipe.instructions,
                recipe.instagram_url,
            ),
        )
        
        