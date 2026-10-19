import json

from backend.tenancy import validate_dietitian_id

from backend.persistence.database import Database
from backend.models.recipe import RecipeEmbedding
from uuid import UUID


class RecipeEmbeddingRepository:
    def __init__(self, db: Database, dietitian_id: str):
        self.db = db
        self.dietitian_id = validate_dietitian_id(dietitian_id)

    def get_embeddings(self, recipe_id: UUID) -> list[RecipeEmbedding]:
        rows = self.db.fetch_all(
            """
            SELECT
                recipe_id,
                embedding::text AS embedding,
                model,
                created_at
            FROM recipe_embeddings
            WHERE dietitian_id = %s AND recipe_id = %s
            ORDER BY created_at
            """,
            (self.dietitian_id, recipe_id),
        )
        return [RecipeEmbedding(**{**row, "embedding": json.loads(row["embedding"])}) for row in rows]

    def find_most_similar_recipes(
        self,
        query_embedding: list[float],
        candidate_ids: list[UUID] | None = None,
        top_k: int = 5,
    ) -> list[tuple[UUID, float]]:
        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

        if candidate_ids:
            rows = self.db.fetch_all(
                """
                SELECT
                    recipe_id,
                    embedding <=> %s::vector AS distance
                FROM recipe_embeddings
                WHERE dietitian_id = %s AND recipe_id = ANY(%s)
                ORDER BY distance
                LIMIT %s
                """,
                (
                    embedding_str,
                    self.dietitian_id,
                    candidate_ids,
                    top_k,
                ),
            )
        else:
            rows = self.db.fetch_all(
                """
                SELECT
                    recipe_id,
                    embedding <=> %s::vector AS distance
                FROM recipe_embeddings
                WHERE dietitian_id = %s
                ORDER BY distance
                LIMIT %s
                """,
                (
                    embedding_str,
                    self.dietitian_id,
                    top_k,
                ),
            )

        return [(row["recipe_id"], row["distance"]) for row in rows]

    def create(
        self,
        recipe_id: UUID,
        embedding: list[float],
        model: str,
        db=None,
    ) -> None:
        embedding_string = "[" + ",".join(map(str, embedding)) + "]"

        conn = db or self.db
        conn.execute(
            """
            INSERT INTO recipe_embeddings (
                dietitian_id,
                recipe_id,
                embedding,
                model
            )
            VALUES (%s, %s, %s::vector, %s)
            """,
            (
                self.dietitian_id,
                recipe_id,
                embedding_string,
                model,
            ),
        )
