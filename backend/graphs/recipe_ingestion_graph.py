from backend.tenancy import TenantContext, load_tenant

# Graph/RecipeIngestionGraph.py

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.factories.model_registry import ModelRegistry

from langgraph.graph import StateGraph, START, END

from backend.graphs.graph_state import RecipeIngestionState
from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import RecipeIngestionRequest

from backend.nodes.ingestion.extract_recipe_node import ExtractRecipeNode
from backend.nodes.ingestion.prepare_embedding_node import PrepareEmbeddingNode
from backend.nodes.ingestion.embedding_node import EmbeddingNode
from backend.nodes.ingestion.persist_recipe_node import PersistRecipeNode


class RecipeIngestionGraph(BaseGraph[RecipeIngestionRequest, RecipeIngestionState]):

    def __init__(
        self,
        extract_node: ExtractRecipeNode,
        prepare_embedding_node: PrepareEmbeddingNode,
        embedding_node: EmbeddingNode,
        persist_node: PersistRecipeNode,
        model_registry: "ModelRegistry | None" = None,
        tenant: TenantContext | None = None,
    ):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.model_registry = model_registry
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

    async def arun(self, request: RecipeIngestionRequest) -> RecipeIngestionState:
        if request.dietitian_id != self.tenant.dietitian_id:
            raise ValueError("Request dietitian does not match this graph deployment")
        return await self.graph.ainvoke({
            "raw_recipe": request.raw_recipe,
            "dietitian_id": request.dietitian_id,
        })
