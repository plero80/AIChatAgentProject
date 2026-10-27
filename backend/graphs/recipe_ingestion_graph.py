from backend.tenancy import TenantContext, load_tenant

# Graph/RecipeIngestionGraph.py

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.factories.model_registry import ModelRegistry

from langgraph.graph import StateGraph, START, END

from backend.graphs.graph_state import RecipeIngestionState
from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import RecipeIngestionRequest
from backend.workflows.build_trace import GraphBuildTrace, qualified_name

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
        with GraphBuildTrace(workflow="recipe_ingestion", source="python", client_id=self.tenant.dietitian_id) as trace:
            self.build_trace_path = trace.path
            return self._build_ingestion_graph(trace)

    def _build_ingestion_graph(self, trace):
        trace.stage("state_schema")
        trace.schema(RecipeIngestionState)

        builder = StateGraph(
            RecipeIngestionState
        )
        trace.stage("nodes")
        for name, node in (
            ("extract", self.extract_node), ("prepare_embedding", self.prepare_embedding_node),
            ("create_embedding", self.embedding_node), ("persist", self.persist_node),
        ):
            builder.add_node(name, node)
            trace.record("node_added", node_id=name, implementation=qualified_name(node))
        trace.stage("connections")
        for source, target in (
            (START, "extract"), ("extract", "prepare_embedding"),
            ("prepare_embedding", "create_embedding"), ("create_embedding", "persist"), ("persist", END),
        ):
            builder.add_edge(source, target)
            trace.record("edge_added", from_node="START" if source == START else source, to_node="END" if target == END else target)
        trace.stage("compile", checkpointer=None)
        graph = builder.compile()
        trace.record("graph_compiled", state_schema="RecipeIngestionState")
        return graph

    async def arun(self, request: RecipeIngestionRequest) -> RecipeIngestionState:
        if request.dietitian_id != self.tenant.dietitian_id:
            raise ValueError("Request dietitian does not match this graph deployment")
        return await self.graph.ainvoke({
            "raw_recipe": request.raw_recipe,
            "dietitian_id": request.dietitian_id,
        })
