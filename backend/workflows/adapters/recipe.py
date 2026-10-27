"""Translate the existing recipe-style graph boundary without changing its flow."""

import logging

from backend.clients.context import ClientContext
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.graphs.graph_requests import ChatGraphRequest
from backend.models.chat_response import ChatResponse
from backend.services.chat.recipe_presentation_service import build_text_response
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.workflows.image_input import ImageInputUnsupported


logger = logging.getLogger(__name__)


class RecipeWorkflowAdapter(ChatWorkflow):
    def __init__(self, graph: BaseChatGraph, *, client: ClientContext):
        super().__init__(client)
        self.graph = graph

    async def _respond(self, request: ConversationRequest) -> ChatResponse:
        if request.images:
            raise ImageInputUnsupported("Photos must be processed by the guarded workflow first")
        result = await self.graph.arun(ChatGraphRequest(
            message=request.message,
            user_id=request.conversation_id,
            dietitian_id=request.client_id,
        ))
        reply = result.get("final_response") or ""
        blocks = result.get("response_blocks")
        # Preserve explicitly empty blocks; only missing/None uses the fallback.
        response = (
            ChatResponse(reply=reply, blocks=blocks)
            if blocks is not None
            else build_text_response(reply)
        )
        analysis = result.get("analysis")
        logger.info(
            "recipe client=%s conversation=%s intent=%s mode=%s",
            request.client_id, request.conversation_id,
            getattr(analysis, "intent", "?"),
            getattr(analysis, "recipe_mode", "?"),
        )
        return response
