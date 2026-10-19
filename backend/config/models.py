"""Named chat-model profiles and the profile assigned to each LLM consumer."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from backend.config.settings import Settings, settings as app_settings
from backend.config.services import ImplementationName, ServiceConfig, ServiceConfigurationError, load_service_config
from backend.paths import CONFIG_DIR


NonEmptyString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
DEFAULT_MODELS_CONFIG = CONFIG_DIR / "models.json"


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ImplementationName
    model: NonEmptyString
    max_tokens: Annotated[int, Field(strict=True, gt=0)] = 16000
    reasoning_effort: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_provider_options(self) -> Self:
        if self.reasoning_effort is not None and self.provider != "openai":
            raise ValueError("reasoning_effort is only supported by the openai adapter")
        return self


class ModelAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_analyzer: ImplementationName = "default"
    request_validator: ImplementationName = "default"
    recipe_generation: ImplementationName = "default"
    recipe_extractor: ImplementationName = "default"
    chat: ImplementationName = "default"
    response: ImplementationName = "default"


MODEL_ENV_VARS = {role: f"{role.upper()}_MODEL_PROFILE" for role in ModelAssignments.model_fields}


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    models: dict[ImplementationName, ModelProfile]
    assignments: ModelAssignments = Field(default_factory=ModelAssignments)

    @model_validator(mode="after")
    def validate_assignments(self) -> Self:
        for role, name in self.assignments.model_dump().items():
            if name not in self.models:
                raise ValueError(f"{role} refers to unknown model profile {name!r}")
        return self


def load_model_config(
    path: str | Path | None = None,
    *,
    service_config: ServiceConfig | None = None,
    overrides: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    settings: Settings = app_settings,
) -> ModelConfig:
    """Resolve assignments: CLI > environment > JSON > default profile.

    Unless JSON defines it explicitly, the default profile uses the existing
    service chat_model choice and CHAT_MODEL / CHAT_MAX_TOKENS settings.
    Explicit profiles are self-contained and never inherit another provider's options.
    """
    env = os.environ if environ is None else environ
    services = service_config if service_config is not None else load_service_config(environ=env)
    config_path = Path(path if path is not None else env.get("MODELS_CONFIG", DEFAULT_MODELS_CONFIG))
    try:
        values = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ServiceConfigurationError(f"Cannot read model config {config_path}: {exc}") from exc
    if not isinstance(values, dict):
        raise ServiceConfigurationError(f"Model config {config_path} must be a JSON object")

    profiles = values.get("models", {})
    assignments = values.get("assignments", {})
    if not isinstance(profiles, dict) or not isinstance(assignments, dict):
        raise ServiceConfigurationError("Model config 'models' and 'assignments' must be JSON objects")

    default_profile = {
        "provider": services.chat_model,
        "model": settings.CHAT_MODEL,
        "max_tokens": settings.CHAT_MAX_TOKENS,
        "reasoning_effort": settings.OPENAI_REASONING_EFFORT if services.chat_model == "openai" else None,
    }
    values["models"] = {"default": default_profile, **profiles}
    values["assignments"] = {
        **assignments,
        **{role: env[var] for role, var in MODEL_ENV_VARS.items() if var in env},
        **(overrides or {}),
    }
    try:
        return ModelConfig.model_validate(values)
    except ValidationError as exc:
        raise ServiceConfigurationError(f"Invalid model config: {exc}") from exc
