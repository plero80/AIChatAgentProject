# Nodes/Ingestion/EmbeddingNode.py

from langchain_core.embeddings import Embeddings

from Graph.GraphState import RecipeIngestionState


class EmbeddingNode:

    def __init__(
        self,
        embedding_model: Embeddings,
    ):
        self.embedding_model = embedding_model

    def __call__(
        self,
        state: RecipeIngestionState,
    ) -> dict:

        embedding = self.embedding_model.embed_query(
            state["embedding_text"]
        )

        return {
            "embedding": embedding
        }