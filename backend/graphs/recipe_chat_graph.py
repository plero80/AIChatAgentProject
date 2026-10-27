from backend.tenancy import TenantContext, load_tenant

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.factories.model_registry import ModelRegistry

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langchain_core.messages import HumanMessage

from backend.graphs.graph_state import GraphState
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.recipe_chat_nodes import RecipeChatNodes
from backend.workflows.build_trace import GraphBuildTrace, qualified_name


class RecipeChatGraph(BaseChatGraph):
    """Business workflow for admitted requests; public startup wraps its adapter."""

    def __init__(
        self,
        nodes: RecipeChatNodes,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        model_registry: "ModelRegistry | None" = None,
        tenant: TenantContext | None = None,
    ):
        # Retain the startup registry for inspecting/reusing the initialized clients.
        self.tenant = tenant if tenant is not None else load_tenant()
        self.model_registry = model_registry
        # Falls back to in-process memory when no durable store is provided
        self.memory = checkpointer if checkpointer is not None else InMemorySaver()
        self.nodes = nodes
        self.graph = self.build_graph()

    def build_graph(self):
        with GraphBuildTrace(workflow="recipe_chat", source="python", client_id=self.tenant.dietitian_id) as trace:
            self.build_trace_path = trace.path
            return self._build_python_graph(trace)

    def _build_python_graph(self, trace):
        trace.stage("state_schema")
        trace.schema(GraphState)
        workflow = StateGraph(GraphState)
        trace.stage("nodes")
        for name, node in (
            ("analyze", self.nodes.analysis), ("chat", self.nodes.chat),
            ("recipe", self.nodes.recipe), ("dietitian", self.nodes.dietitian),
            ("response", self.nodes.response),
        ):
            workflow.add_node(name, node)
            trace.record("node_added", node_id=name, implementation=qualified_name(node))
        trace.stage("connections")
        for source, target in (
            (START, "analyze"), ("chat", "response"), ("recipe", "response"),
            ("dietitian", "response"), ("response", END),
        ):
            workflow.add_edge(source, target)
            trace.record("edge_added", from_node="START" if source == START else source, to_node="END" if target == END else target)
        workflow.add_conditional_edges(
            "analyze",
            self.route_after_analysis,
            {"chat": "chat", "recipe": "recipe", "dietitian": "dietitian"},
        )
        trace.record(
            "route_added", from_node="analyze", router=qualified_name(self.route_after_analysis),
            paths={"chat": "chat", "recipe": "recipe", "dietitian": "dietitian"},
        )
        trace.stage("compile", checkpointer=qualified_name(self.memory))
        graph = workflow.compile(checkpointer=self.memory)
        trace.record("graph_compiled", state_schema="GraphState")
        return graph

    @staticmethod
    def route_after_analysis(state: GraphState) -> str:
        analysis = state.get("analysis")
        if analysis is not None and analysis.intent == "food":
            return "recipe"
        if analysis is not None and analysis.intent == "dietitian_escort":
            return "dietitian"
        return "chat"

    async def arun(self, request: ChatGraphRequest) -> GraphState:
        if request.dietitian_id != self.tenant.dietitian_id:
            raise ValueError("Request dietitian does not match this graph deployment")

        initial_state = {
            "user_id": request.user_id,
            "dietitian_id": request.dietitian_id,
            "message": request.message,
            # Checkpoints preserve state across turns. Presentation and routing
            # data belong only to this request; never reuse a previous photo.
            "analysis": None,
            "validation": None,
            "result": None,
            "final_response": None,
            "response_blocks": None,
            "messages": [
                HumanMessage(content=request.message)
            ],
        }

        config = {
            "configurable": {
                "thread_id": request.thread_id
            }
        }

        result = await self.graph.ainvoke(
            initial_state,
            config=config,
        )

        return result
