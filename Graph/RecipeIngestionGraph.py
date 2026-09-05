# Graph/RecipeIngestionGraph.py

from langgraph.graph import StateGraph, START, END

from Graph.GraphState import RecipeIngestionState

from Nodes.Ingestion.ExtractRecipeNode import ExtractRecipeNode
from Nodes.Ingestion.PrepareEmbeddingNode import PrepareEmbeddingNode
from Nodes.Ingestion.EmbeddingNode import EmbeddingNode
from Nodes.Ingestion.PersistRecipeNode import PersistRecipeNode


class RecipeIngestionGraph:

    def __init__(
        self,
        extract_node: ExtractRecipeNode,
        prepare_embedding_node: PrepareEmbeddingNode,
        embedding_node: EmbeddingNode,
        persist_node: PersistRecipeNode,
    ):
        self.extract_node = extract_node
        self.prepare_embedding_node = prepare_embedding_node
        self.embedding_node = embedding_node
        self.persist_node = persist_node

        self.graph = self.build_graph()

    def build_graph(self):

        builder = StateGraph(
            RecipeIngestionState
        )

        builder.add_node(
            "extract",
            self.extract_node,
        )

        builder.add_node(
            "prepare_embedding",
            self.prepare_embedding_node,
        )

        builder.add_node(
            "create_embedding",
            self.embedding_node,
        )

        builder.add_node(
            "persist",
            self.persist_node,
        )

        builder.add_edge(
            START,
            "extract",
        )

        builder.add_edge(
            "extract",
            "prepare_embedding",
        )

        builder.add_edge(
            "prepare_embedding",
            "create_embedding",
        )

        builder.add_edge(
            "create_embedding",
            "persist",
        )

        builder.add_edge(
            "persist",
            END,
        )

        return builder.compile()

    def run(
        self,
        raw_recipe: str,
        instagram_url: str | None = None,
    ) -> RecipeIngestionState:

        return self.graph.invoke({
            "raw_recipe": raw_recipe,
            "instagram_url": instagram_url,
        })