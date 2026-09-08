from Database.Database import Database
from Models.Recipe import RecipeEmbedding
from uuid import UUID


class RecipeEmbeddingRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_embeddings(self, recipe_id: UUID) -> list[RecipeEmbedding]:
        rows = self.db.fetch_all(
            """
            SELECT
                recipe_id,
                embedding,
                model,
                created_at
            FROM recipe_embeddings
            WHERE recipe_id = %s
            ORDER BY created_at
            """,
            (recipe_id,),
        )
        return [RecipeEmbedding(**row) for row in rows]

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
                WHERE recipe_id = ANY(%s)
                ORDER BY distance
                LIMIT %s
                """,
                (
                    embedding_str,
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
                ORDER BY distance
                LIMIT %s
                """,
                (
                    embedding_str,
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
                recipe_id,
                embedding,
                model
            )
            VALUES (%s, %s::vector, %s)
            """,
            (
                recipe_id,
                embedding_string,
                model,
            ),
        )
