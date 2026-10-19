"""The node container produced by RecipeChatBuilder."""

from dataclasses import dataclass

from backend.nodes.chat.analyze_node import AnalyzeNode
from backend.nodes.chat.chat_node import ChatNode
from backend.nodes.chat.dietitian_node import DietitianNode
from backend.nodes.chat.guard_node import GuardNode
from backend.nodes.chat.recipe_node import RecipeNode
from backend.nodes.chat.response_node import ResponseNode


@dataclass(frozen=True)
class RecipeChatNodes:
    analysis: AnalyzeNode
    recipe: RecipeNode
    chat: ChatNode
    response: ResponseNode
    dietitian: DietitianNode
    guard: GuardNode
