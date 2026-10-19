"""Immutable configuration for the one dietitian represented by a deployment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, TypeAdapter

from backend.paths import CONFIG_DIR, DATA_DIR
# Load .env through the same entry point as the other configuration modules.
from backend.config.settings import settings as _settings  # noqa: F401


DEFAULT_DIETITIAN_ID = "clinic_001"
DietitianId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ImageName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)$")]
_dietitian_id_adapter = TypeAdapter(DietitianId)


class FrozenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Starter(FrozenConfig):
    label: NonEmpty
    message: NonEmpty


class Inspiration(Starter):
    image: ImageName


class Branding(FrozenConfig):
    brand_name: NonEmpty
    title: NonEmpty
    subtitle: NonEmpty
    welcome: NonEmpty
    footer: NonEmpty
    accent_color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] = "#506e43"
    starters: tuple[Starter, ...] = ()
    inspiration: tuple[Inspiration, ...] = ()


class DietitianProfile(FrozenConfig):
    dietitian_id: DietitianId
    display_name: NonEmpty
    name_he: NonEmpty
    assistant_name: NonEmpty
    aliases: tuple[NonEmpty, ...] = Field(min_length=1)
    contact_url: HttpUrl | None = None
    branding: Branding


# Each file has an explicit contract, so a misspelled placeholder fails startup.
PROMPT_ARGUMENTS = {
    "analyze": set(), "validate": set(), "chat": set(),
    "recipe_presentation": set(), "general_response": set(), "extract_recipe": set(),
    "generate_original": {"ingredients", "preferences"},
    "generate_inspired": {"ingredients", "preferences", "reference_recipes"},
    "refusal": set(), "recipe_not_found": set(), "dietitian_contact": {"contact_url"},
}
PROFILE_ARGUMENTS = {"display_name", "name_he", "assistant_name"}


def validate_dietitian_id(value: str) -> str:
    return _dietitian_id_adapter.validate_python(value)


@dataclass(frozen=True)
class TenantContext:
    profile: DietitianProfile
    prompts: Mapping[str, str]
    images_dir: Path

    @property
    def dietitian_id(self) -> str:
        return self.profile.dietitian_id

    def render(self, name: str, **arguments: object) -> str:
        if set(arguments) != PROMPT_ARGUMENTS[name]:
            raise ValueError(f"Incorrect arguments for prompt {name!r}")
        values = {key: getattr(self.profile, key) for key in PROFILE_ARGUMENTS}
        return Template(self.prompts[name]).substitute(values, **arguments).strip()

    def public_config(self) -> dict:
        # Deliberate allowlist: no prompts, model configuration, or credentials.
        return {
            "dietitian_id": self.dietitian_id,
            "assistant_name": self.profile.name_he,
            **self.profile.branding.model_dump(mode="json"),
        }


def load_tenant(
    dietitian_id: str | None = None, *, environ: Mapping[str, str] | None = None,
    config_dir: Path = CONFIG_DIR, data_dir: Path = DATA_DIR,
) -> TenantContext:
    """Read and validate once at startup; services retain this snapshot until restart."""
    env = os.environ if environ is None else environ
    selected = dietitian_id if dietitian_id is not None else env.get("DIETITIAN_ID")
    if selected is None:
        if env.get("RAILWAY_ENVIRONMENT") or env.get("PORT"):
            raise ValueError("Set DIETITIAN_ID explicitly for this deployment")
        selected = DEFAULT_DIETITIAN_ID
    selected = validate_dietitian_id(selected)
    directory = config_dir / "dietitians" / selected
    profile_path = directory / "profile.json"
    if not profile_path.is_file():
        raise ValueError(f"Unknown DIETITIAN_ID {selected!r}: missing {profile_path}")
    profile = DietitianProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    if profile.dietitian_id != selected:
        raise ValueError("profile.json dietitian_id must match its directory and DIETITIAN_ID")
    overrides = directory / "prompts"
    unknown = {path.stem for path in overrides.glob("*.txt")} - PROMPT_ARGUMENTS.keys()
    if unknown:
        raise ValueError(f"Unknown prompt overrides: {sorted(unknown)}")
    prompts = {}
    for name, arguments in PROMPT_ARGUMENTS.items():
        path = overrides / f"{name}.txt"
        if not path.is_file():
            path = config_dir / "prompts" / f"{name}.txt"
        content = path.read_text(encoding="utf-8").strip()
        template = Template(content)
        if not content or not template.is_valid():
            raise ValueError(f"Empty or invalid prompt template: {path}")
        unknown = set(template.get_identifiers()) - PROFILE_ARGUMENTS - arguments
        if unknown:
            raise ValueError(f"Unknown placeholders in {path}: {sorted(unknown)}")
        prompts[name] = content
    return TenantContext(
        profile, MappingProxyType(prompts), data_dir / "dietitians" / selected / "recipe_images",
    )
