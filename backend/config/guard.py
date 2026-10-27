"""Trusted deployment admission policy, loaded before opening external resources."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.config.services import ImplementationName, ServiceConfigurationError
from backend.guards.contracts import GuardPolicy
from backend.paths import CONFIG_DIR
from backend.tenancy import TenantContext


class GuardConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    evaluator: ImplementationName = "decisions"
    policy: GuardPolicy
    history_messages: int = Field(default=6, ge=0, le=50)
    history_characters: int = Field(default=6000, ge=0, le=50000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def load_guard_config(tenant: TenantContext, *, path: str | Path | None = None, environ=None, config_dir=CONFIG_DIR) -> GuardConfig:
    env = os.environ if environ is None else environ
    selected = path if path is not None else env.get("GUARD_CONFIG")
    if selected is None:
        override = Path(config_dir) / "dietitians" / tenant.dietitian_id / "guard.json"
        selected = override if override.is_file() else Path(config_dir) / "guard.json"
    try:
        return GuardConfig.model_validate_json(Path(selected).read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError):
        # Config may include private instructions; do not include raw JSON/values.
        raise ServiceConfigurationError(f"Cannot load valid guard configuration from {selected}") from None
