"""One entry point for building, caching, and invoking client workflows."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from backend.clients.context import ClientContext
from backend.models.chat_response import ChatResponse
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.workflows.runtime import WorkflowFactory, WorkflowRuntimeCache


@dataclass
class _ConversationLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class WorkflowFacade:
    """Accept server-resolved identity; keep graph caching separate from history.

    A release ID must describe immutable configuration. The application resolves
    and pins it before calling this facade. The facade neither authenticates users
    nor changes the existing checkpoint keys.
    """

    def __init__(self, factory: WorkflowFactory, *, max_cached_workflows: int = 16):
        self.cache = WorkflowRuntimeCache(factory, max_entries=max_cached_workflows)
        self._conversations: dict[tuple[str, str], _ConversationLock] = {}

    def for_client(self, client: ClientContext) -> ChatWorkflow:
        """A lightweight public interface; construction happens on warm/first use."""
        return _ClientWorkflow(self, client)

    async def warm(self, client: ClientContext) -> None:
        await self.cache.warm(client)

    async def respond(self, client: ClientContext, request: ConversationRequest) -> ChatResponse:
        if request.client_id != client.client_id or request.release_id != client.release_id:
            raise ValueError("Request does not match the server-resolved client/release")
        # Acquire the runtime first to establish the cache's event-loop ownership.
        async with self.cache.acquire(client) as workflow:
            async with self._conversation_turn(request):
                return await workflow.respond(request)

    @asynccontextmanager
    async def _conversation_turn(self, request):
        # Release is deliberately excluded: legacy checkpoints also exclude it.
        key = (request.client_id, request.conversation_id)
        entry = self._conversations.setdefault(key, _ConversationLock())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                del self._conversations[key]

    async def aclose(self) -> None:
        await self.cache.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()


class _ClientWorkflow(ChatWorkflow):
    def __init__(self, facade: WorkflowFacade, client: ClientContext):
        super().__init__(client)
        self.facade = facade

    async def _respond(self, request: ConversationRequest) -> ChatResponse:
        return await self.facade.respond(self.client, request)
