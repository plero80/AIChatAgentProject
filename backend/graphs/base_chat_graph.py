"""Chat workflow contract shared by the HTTP API and interactive CLI."""

from abc import abstractmethod

from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.graph_state import GraphState


class BaseChatGraph(BaseGraph[ChatGraphRequest, GraphState]):
    @abstractmethod
    async def arun(self, request: ChatGraphRequest) -> GraphState:
        """Handle a chat turn and return final_response and response_blocks.

        Preserve user_id isolation when storing conversation history. Workflow
        topology and node selection belong to the concrete implementation.
        """
