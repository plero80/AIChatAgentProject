"""A chat caller supplies conversation identity and receives a public response."""

from abc import ABC, abstractmethod
from typing import Annotated

from pydantic import ConfigDict, StringConstraints

from backend.clients.context import ClientContext, ClientId, NonEmptyText
from backend.models.chat_response import ChatResponse
from backend.models.chat_input import ChatContent


class ConversationRequest(ChatContent):
    model_config = ConfigDict(frozen=True, extra="forbid")
    # Public text is capped at 16k; reserve space for bounded image observations.
    message: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=64000)] = ""

    client_id: ClientId
    # One browser/CLI session is one conversation in the current application.
    conversation_id: NonEmptyText
    release_id: NonEmptyText = "legacy"

    @property
    def thread_id(self) -> str:
        """Keep existing checkpoints addressable during the boundary migration."""
        return f"{self.client_id}:{self.conversation_id}"


class ChatWorkflow(ABC):
    def __init__(self, client: ClientContext):
        self.client = client

    async def respond(self, request: ConversationRequest) -> ChatResponse:
        """Check ownership before execution and validate the public output."""
        if request.client_id != self.client.client_id:
            raise ValueError("Request client does not match this workflow")
        if request.release_id != self.client.release_id:
            raise ValueError("Request release does not match this workflow")
        return ChatResponse.model_validate(await self._respond(request))

    @abstractmethod
    async def _respond(self, request: ConversationRequest) -> ChatResponse:
        """Implement a turn; internal state and execution topology are private."""
