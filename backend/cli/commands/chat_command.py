from argparse import ArgumentParser, Namespace
from collections.abc import Callable
from uuid import uuid4

from backend.cli.commands.base_command import BaseCommand
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.graphs.graph_requests import ChatGraphRequest


class ChatCommand(BaseCommand):
    description = "Start an interactive chat (exit or quit to finish)."

    def __init__(self, graph_factory: Callable[[], BaseChatGraph]):
        self.graph_factory = graph_factory

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--user-id", help="Conversation ID; defaults to a new ID for this session")

    async def run(self, args: Namespace) -> int:
        graph = self.graph_factory()
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
            result = await graph.arun(ChatGraphRequest(message=message, user_id=user_id))
            print("Alona:", result["final_response"])
