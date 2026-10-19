"""Archived early graph-state draft; the active state is backend.graphs.graph_state."""

from typing_extensions import TypedDict
from backend.models.message_analysis import MessageAnalysis
from langchain_core.messages import BaseMessage
from typing import Annotated
from langgraph.graph.message import add_messages

class GraphState(TypedDict):
    user_id: str

    # Current message
    message: str

    # Result of message understanding
    analysis: MessageAnalysis | None

    # Extra temporary information
    context: dict

    # Result produced by recipe / chat / intake
    result: dict | None

    # Conversation
    messages: Annotated[list[BaseMessage], add_messages]

    # Response
    final_response: str | None
