"""Task-specific input/output handling around the common graph contract."""

from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace


class BaseCommand(ABC):
    description: str

    @abstractmethod
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Declare the task's command-line inputs."""

    @abstractmethod
    async def run(self, args: Namespace) -> int:
        """Prepare input, execute the graph, and display output; return an exit code."""
