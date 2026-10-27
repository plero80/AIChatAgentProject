from argparse import ArgumentParser, Namespace
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from uuid import uuid4

from backend.cli.commands.base_command import BaseCommand
from backend.workflows.contracts import ChatWorkflow, ConversationRequest


class ChatCommand(BaseCommand):
    description = "Start an interactive chat (exit or quit to finish)."

    def __init__(self, workflow_factory: Callable[[], ChatWorkflow | AbstractAsyncContextManager[ChatWorkflow]]):
        self.workflow_factory = workflow_factory

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--user-id", help="Conversation ID; defaults to a new ID for this session")

    async def run(self, args: Namespace) -> int:
        async with AsyncExitStack() as resources:
            created = self.workflow_factory()
            # Already-built workflows remain caller-owned (tests/embedded CLI).
            workflow = created if isinstance(created, ChatWorkflow) else await resources.enter_async_context(created)
            return await self._chat(args, workflow)

    async def _chat(self, args: Namespace, workflow: ChatWorkflow) -> int:
        client = workflow.client
        user_id = args.user_id or str(uuid4())
        while True:
            try:
                message = input("You: ").strip()
            except EOFError:
                return 0
            if message.lower() in {"exit", "quit"}:
                return 0
            if not message:
                continue
            result = await workflow.respond(ConversationRequest(
                message=message,
                conversation_id=user_id,
                client_id=client.client_id,
                release_id=client.release_id,
            ))
            print(f"{client.display_name}:", result.reply)
