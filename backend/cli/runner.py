"""Dispatch registered graph commands through one command-line entry point."""

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence

from backend.cli.commands.base_command import BaseCommand
from backend.cli.options import add_service_arguments, configure_service_args


def run_cli(argv: Sequence[str] | None, *, commands: Mapping[str, BaseCommand]) -> int:
    # Collect shared flags across the whole command line. This supports flags
    # before/after the command and preserves repeated --service/--model values.
    service_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    add_service_arguments(service_parser)
    service_args, command_args = service_parser.parse_known_args(argv)

    parser = argparse.ArgumentParser(
        description="Run a graph task.", parents=[service_parser], allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", title="tasks")
    for name, command in commands.items():
        task_parser = subparsers.add_parser(
            name, help=command.description, description=command.description,
            parents=[service_parser], allow_abbrev=False,
        )
        command.add_arguments(task_parser)
    args = parser.parse_args(command_args)

    if args.command is None and not (service_args.show_services or service_args.show_models):
        if not (sys.argv[1:] if argv is None else argv):
            parser.print_help()
            return 0
        parser.error("choose a task: " + ", ".join(commands))

    if not configure_service_args(service_args, parser):
        return 0
    try:
        return asyncio.run(commands[args.command].run(args))
    except KeyboardInterrupt:
        print()
        return 130
