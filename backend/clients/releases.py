"""Portable release documents. Publishing freezes the complete dependency snapshot."""

import hashlib
import json
from dataclasses import dataclass
from string import Template
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, HttpUrl, StringConstraints, field_validator, model_validator

from backend.clients.context import ClientContext
from backend.config.guard import GuardConfig
from backend.config.images import ImageRecognitionConfig
from backend.models.chat_input import image_upload_capabilities
from backend.config.models import ModelProfile
from backend.config.workflows import Name, StrictSpec, WorkflowSpec
from backend.tenancy import Branding, NonEmpty


Slug = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class ClientProfile(StrictSpec):
    display_name: NonEmpty
    name_he: NonEmpty
    assistant_name: NonEmpty
    aliases: list[NonEmpty] = Field(min_length=1)
    contact_url: HttpUrl | None = None
    branding: Branding


class PromptVersion(StrictSpec):
    version: int = Field(gt=0)
    text: NonEmpty

    @model_validator(mode="after")
    def valid_template(self):
        if not Template(self.text).is_valid():
            raise ValueError("Invalid prompt template")
        return self


class ReleaseSpec(StrictSpec):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")
    schema_version: Literal[1] = 1
    client_id: Name
    profile: ClientProfile
    workflow: WorkflowSpec
    models: dict[Name, ModelProfile] = Field(min_length=1, max_length=64)
    prompts: dict[Name, PromptVersion] = Field(max_length=256)
    guard: GuardConfig
    # Omit absent capability on serialization to preserve fingerprints of old releases.
    image_recognition: ImageRecognitionConfig | None = Field(default=None, exclude_if=lambda value: value is None)
    # Recipe vector dimensions are fixed by the existing storage schema.
    embedding_model: NonEmpty | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    def fingerprint(self) -> str:
        document = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(document.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    slug: str
    business_type: str
    active_release_id: str | None


@dataclass(frozen=True)
class PublishedRelease:
    client: ClientRecord
    release_id: str
    version: int
    spec: ReleaseSpec

    @property
    def context(self) -> ClientContext:
        return ClientContext(
            client_id=self.client.client_id,
            display_name=self.spec.profile.branding.brand_name,
            release_id=self.release_id,
        )

    def public_config(self) -> dict:
        return {
            "client_id": self.client.client_id,
            "client_slug": self.client.slug,
            "release_id": self.release_id,
            "assistant_name": self.spec.profile.name_he,
            "image_base_url": f"/api/clients/{self.client.slug}/recipe-images",
            "image_upload": image_upload_capabilities(self.spec.image_recognition is not None),
            **self.spec.profile.branding.model_dump(mode="json"),
        }
