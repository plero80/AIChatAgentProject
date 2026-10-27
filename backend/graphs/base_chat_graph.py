"""Internal graph contract retained behind the public chat workflow adapter."""

from abc import abstractmethod

from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.graph_state import GraphState


class BaseChatGraph(BaseGraph[ChatGraphRequest, GraphState]):
    @abstractmethod
    async def arun(self, request: ChatGraphRequest) -> GraphState:
        """Handle a chat turn and return final_response and response_blocks.

        Reject requests for a different deployment dietitian. Use request.thread_id
        (dietitian_id + user_id) for conversation checkpoints, including in new
        graph implementations. Workflow topology belongs to the implementation.
        """
