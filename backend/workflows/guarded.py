"""Apply a request guard at the workflow boundary shared by API and CLI."""

import asyncio
from collections.abc import Awaitable, Callable
import logging

from backend.guards.request_guard import RequestGuard
from backend.models.chat_response import ChatResponse, TextBlock
from backend.models.chat_input import normalize_chat_images
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.workflows.image_input import ImageInputUnsupported


logger = logging.getLogger(__name__)
HistoryLoader = Callable[[ConversationRequest], Awaitable[str]]


class GuardedWorkflow(ChatWorkflow):
    def __init__(self, workflow: ChatWorkflow, guard: RequestGuard, *, history_loader: HistoryLoader | None = None,
                 image_processor=None):
        if (
            workflow.client.client_id != guard.client.client_id
            or workflow.client.release_id != guard.client.release_id
        ):
            raise ValueError("Workflow client/release does not match this guard")
        super().__init__(workflow.client)
        self.workflow = workflow
        self.guard = guard
        self.history_loader = history_loader
        self.image_processor = image_processor

    async def _respond(self, request: ConversationRequest) -> ChatResponse:
        if request.images and self.image_processor is None:
            raise ImageInputUnsupported("This conversation does not support photos")
        if request.images:
            images = await asyncio.to_thread(normalize_chat_images, request.images)
            request = request.model_copy(update={"images": images})
        # Reading history is allowed before approval; running a checkpoint-writing
        # graph concurrently here would persist rejected work before cancellation.
        try:
            history = (
                await asyncio.wait_for(self.history_loader(request), timeout=self.guard.policy.timeout_seconds)
                if self.history_loader is not None else ""
            )
        except Exception as exc:
            logger.warning("Guard history unavailable error=%s", type(exc).__name__)
            decision = self.guard.unavailable()
        else:
            decision = await self.guard.check(request, history=history)
        logger.info(
            "Admission client=%s release=%s outcome=%s",
            request.client_id, request.release_id, decision.outcome,
        )
        if not decision.allowed:
            return ChatResponse(reply=decision.reply, blocks=[TextBlock(text=decision.reply)])
        if request.images:
            request = await self.image_processor(request)
        return await self.workflow.respond(request)
