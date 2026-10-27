"""Resolve service choices once at startup: overrides > environment > JSON > defaults."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from backend.paths import CONFIG_DIR


ImplementationName = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]*$"),
]
DEFAULT_SERVICES_CONFIG = CONFIG_DIR / "services.json"


class ServiceConfigurationError(ValueError):
    """Invalid service configuration; startup must stop instead of falling back."""


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_graph: ImplementationName = "recipe"
    # Provider for the implicit default model profile; explicit profiles use models.json.
    chat_model: ImplementationName = "openai"
    message_analyzer: ImplementationName = "llm"
    # Legacy selection retained for parsing older configs; chat admission uses guard.json.
    request_validator: ImplementationName = "llm"
    recipe_search: ImplementationName = "postgres"
    recipe_generation: ImplementationName = "llm"
    recipe_extractor: ImplementationName = "llm"
    recipe_ingestion: ImplementationName = "postgres"
    dietitian: ImplementationName = "file"
    recipe_presentation: ImplementationName = "structured"


SERVICE_ENV_VARS = {name: f"{name.upper()}_TYPE" for name in ServiceConfig.model_fields}


def load_service_config(
    path: str | Path | None = None,
    *,
    overrides: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ServiceConfig:
    env = os.environ if environ is None else environ
    config_path = Path(path if path is not None else env.get("SERVICES_CONFIG", DEFAULT_SERVICES_CONFIG))
    try:
        values = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ServiceConfigurationError(f"Cannot read service config {config_path}: {exc}") from exc

    if not isinstance(values, dict):
        raise ServiceConfigurationError(f"Service config {config_path} must be a JSON object")

    values.update({name: env[var] for name, var in SERVICE_ENV_VARS.items() if var in env})
    values.update(overrides or {})
    try:
        return ServiceConfig.model_validate(values)
    except ValidationError as exc:
        raise ServiceConfigurationError(f"Invalid service config: {exc}") from exc
