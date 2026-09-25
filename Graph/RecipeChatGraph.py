import asyncio

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import HumanMessage

from Graph.GraphState import GraphState
from Nodes.AnalyzeNode import AnalyzeNode
from Nodes.GuardNode import GuardNode
from Nodes.ResponseNode import ResponseNode


class RecipeChatGraph:

    def __init__(
        self,
        analyze_node: AnalyzeNode,
        guard_node: GuardNode,
        response_node: ResponseNode,
        checkpointer=None,
    ):
        # Falls back to in-process memory when no durable store is provided
        self.memory = checkpointer or InMemorySaver()

        self.nodes = {
            "analyze": analyze_node,
            "guard": guard_node,
            "response": response_node,
        }

        self.graph = self.build_graph()


    
    def build_graph(self):

        graph = StateGraph(GraphState)

        graph.add_node(
            "analyze",
            self.nodes["analyze"],
        )

        graph.add_node(
            "guard",
            self.nodes["guard"],
        )

        graph.add_node(
            "response",
            self.nodes["response"],
        )

        # START
        graph.add_edge(
            START,
            "analyze",
        )

        # Parent runs validation and the branch together
        graph.add_edge(
            "analyze",
            "guard",
        )

        graph.add_conditional_edges(
            "guard",
            self.route_after_guard,
            {
                "response": "response",
                "end": END,
            },
        )

        graph.add_edge(
            "response",
            END,
        )

        # VERY IMPORTANT
        return graph.compile(
            checkpointer=self.memory
        )

    async def arun(
        self,
        message: str,
        user_id: str,
    ):

        initial_state = {
            "user_id": user_id,
            "message": message,
            "messages": [
                HumanMessage(content=message)
            ],
        }

        config = {
            "configurable": {
                "thread_id": user_id
            }
        }

        result = await self.graph.ainvoke(
            initial_state,
            config=config,
        )

        return result

    def run(
        self,
        message: str,
        user_id: str,
    ):
        """Blocking entry point for the CLI. The nodes are async-only."""
        return asyncio.run(
            self.arun(
                message=message,
                user_id=user_id,
            )
        )

    def route_after_guard(self, state: GraphState) -> str:
        validation = state.get("validation")
        if validation is not None and not validation.legitimate:
            return "end"

        return "response"
