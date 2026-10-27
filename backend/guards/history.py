"""Read bounded, client-scoped conversation evidence without executing the graph."""

from langchain_core.messages import BaseMessage

from backend.clients.context import ClientContext
from backend.workflows.contracts import ConversationRequest


class CheckpointHistoryLoader:
    def __init__(self, checkpointer, *, client: ClientContext, max_messages: int, max_characters: int):
        self.checkpointer = checkpointer
        self.client = client
        self.max_messages = max_messages
        self.max_characters = max_characters

    async def __call__(self, request: ConversationRequest) -> str:
        if request.client_id != self.client.client_id or request.release_id != self.client.release_id:
            raise ValueError("History request does not match the resolved client/release")
        if not self.max_messages or not self.max_characters:
            return ""
        saved = await self.checkpointer.aget_tuple({"configurable": {"thread_id": request.thread_id}})
        if saved is None:
            return ""
        messages = saved.checkpoint["channel_values"].get("messages", [])
        if not isinstance(messages, list):
            raise TypeError("Checkpoint messages must be a list")
        lines = []
        remaining = self.max_characters
        # Prefer the newest context when the character budget is exhausted.
        for message in reversed(messages[-self.max_messages:]):
            if not isinstance(message, BaseMessage):
                raise TypeError("Checkpoint history must contain messages")
            role = {"human": "user", "ai": "assistant"}.get(message.type)
            if role is None:
                continue
            content = message.content
            if isinstance(content, list):
                parts = []
                size = 0
                for block in content[:64]:
                    part = block if isinstance(block, str) else (
                        block.get("text", "") if isinstance(block, dict) and block.get("type") in {"text", "output_text"} else ""
                    )
                    if not isinstance(part, str):
                        raise TypeError("History text blocks must contain text")
                    parts.append(part[:remaining - size])
                    size += len(parts[-1])
                    if size == remaining:
                        break
                content = "".join(parts)
            if not isinstance(content, str):
                raise TypeError("History content must be text")
            line = f"{role}: " + content[:remaining]
            lines.append(line[:remaining])
            remaining -= len(lines[-1]) + 1
            if remaining <= 0:
                break
        return "\n".join(reversed(lines))
