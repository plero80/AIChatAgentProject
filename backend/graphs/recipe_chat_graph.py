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


class RecipeChatGraph(BaseChatGraph):
    """Connect the supplied nodes and run the recipe chat workflow."""

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
        workflow = StateGraph(GraphState)
        workflow.add_node("analyze", self.nodes.analysis)
        workflow.add_node("guard", self.nodes.guard)
        workflow.add_node("response", self.nodes.response)
        workflow.add_edge(START, "analyze")
        # GuardNode runs validation and the selected branch concurrently.
        workflow.add_edge("analyze", "guard")
        workflow.add_conditional_edges(
            "guard",
            self.route_after_guard,
            {"response": "response", "end": END},
        )
        workflow.add_edge("response", END)
        return workflow.compile(checkpointer=self.memory)

    @staticmethod
    def route_after_guard(state: GraphState) -> str:
        validation = state.get("validation")
        if validation is not None and not validation.legitimate:
            return "end"
        return "response"

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
