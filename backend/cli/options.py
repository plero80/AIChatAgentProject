"""Shared startup flags for the API launcher and the existing main.py runner."""

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from backend.factories.service_factories import ServiceFactories
from backend.factories.model_registry import ModelRegistry
from backend.config.models import MODEL_ENV_VARS, load_model_config
from backend.config.services import SERVICE_ENV_VARS, ServiceConfigurationError, load_service_config


def add_service_arguments(parser: argparse.ArgumentParser) -> None:
    """Share the same configuration flags between graph commands and the API."""
    parser.add_argument("--services-config", type=Path, help="JSON file of service implementation names")
    parser.add_argument("--models-config", type=Path, help="JSON file of model profiles and assignments")
    parser.add_argument(
        "--service", action="append", default=[], metavar="NAME=TYPE",
        help="Override one service (repeatable); takes precedence over environment and JSON",
    )
    parser.add_argument("--show-services", action="store_true", help="Print effective choices and available types, then exit")
    parser.add_argument(
        "--model", action="append", default=[], metavar="ROLE=PROFILE",
        help="Assign a named model profile to a role (repeatable)",
    )
    parser.add_argument("--show-models", action="store_true", help="Print resolved model profiles and assignments, then exit")


def configure_service_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    """Validate parsed choices and export them; False means inspect only."""

    overrides = {}
    for value in args.service:
        name, separator, implementation = value.partition("=")
        if not separator or name not in SERVICE_ENV_VARS:
            parser.error(f"--service expects NAME=TYPE; names: {', '.join(SERVICE_ENV_VARS)}")
        overrides[name] = implementation

    model_overrides = {}
    for value in args.model:
        role, separator, profile = value.partition("=")
        if not separator or role not in MODEL_ENV_VARS:
            parser.error(f"--model expects ROLE=PROFILE; roles: {', '.join(MODEL_ENV_VARS)}")
        model_overrides[role] = profile

    factories = ServiceFactories()
    try:
        config = load_service_config(args.services_config, overrides=overrides)
        factories.validate(config)
        models = load_model_config(args.models_config, service_config=config, overrides=model_overrides)
        ModelRegistry.validate(models, factories.chat_model)
    except ServiceConfigurationError as exc:
        parser.error(str(exc))

    if args.show_services or args.show_models:
        print(json.dumps({
            "selected": config.model_dump(), "available": factories.available(),
            "model_config": models.model_dump(),
        }, indent=2))
        return False

    # Uvicorn's reloader imports api in a new process, so it must inherit CLI choices.
    if args.services_config is not None:
        os.environ["SERVICES_CONFIG"] = str(args.services_config.resolve())
    if args.models_config is not None:
        os.environ["MODELS_CONFIG"] = str(args.models_config.resolve())
    for name, implementation in overrides.items():
        os.environ[SERVICE_ENV_VARS[name]] = implementation
    for role, profile in model_overrides.items():
        os.environ[MODEL_ENV_VARS[role]] = profile
    return True


def configure_service_cli(argv: Sequence[str] | None = None) -> bool:
    """Configure the API launcher without selecting a graph command."""
    parser = argparse.ArgumentParser(description="Configure service implementations at startup.")
    add_service_arguments(parser)
    return configure_service_args(parser.parse_args(argv), parser)
