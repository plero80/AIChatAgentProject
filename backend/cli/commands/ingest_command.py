import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Callable
from pathlib import Path

from backend.cli.commands.base_command import BaseCommand
from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import RecipeIngestionRequest
from backend.graphs.graph_state import RecipeIngestionState


class IngestCommand(BaseCommand):
    description = "Extract and save a recipe from a UTF-8 text file."

    def __init__(self, graph_factory: Callable[[], BaseGraph[RecipeIngestionRequest, RecipeIngestionState]]):
        self.graph_factory = graph_factory

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--file", type=Path, required=True, help="Recipe text file to ingest")

    async def run(self, args: Namespace) -> int:
        try:
            raw_recipe = args.file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            print(f"Cannot read recipe file {args.file}: {exc}", file=sys.stderr)
            return 1
        if not raw_recipe.strip():
            print(f"Recipe file is empty: {args.file}", file=sys.stderr)
            return 1

        graph = self.graph_factory()
        result = await graph.arun(RecipeIngestionRequest(raw_recipe=raw_recipe))
        if not result.get("success"):
            print(result.get("error") or "Recipe ingestion failed", file=sys.stderr)
            return 1
        print(f"Recipe saved: {result['recipe_id']}")
        return 0
