from typing_extensions import TypedDict
from Models.MessageAnalysis import MessageAnalysis
from langchain_core.messages import BaseMessage
from typing import Annotated
from langgraph.graph.message import add_messages

from uuid import UUID

from Models.RecipeCreate import RecipeCreate

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




class RecipeIngestionState(TypedDict, total=False):

    # INPUT
    raw_recipe: str
    instagram_url: str | None

    # Created by AI
    recipe: RecipeCreate

    # Embedding
    embedding_text: str
    embedding: list[float]

    # Database
    recipe_id: UUID

    success: bool
    error: str | None