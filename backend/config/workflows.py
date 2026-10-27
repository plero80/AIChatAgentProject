"""The versioned, data-only format accepted by the workflow compiler."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator


Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Endpoint = Name | Literal["START", "END"]


class WorkflowConfigurationError(ValueError):
    """A definition cannot safely be assembled into a supported workflow."""


class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ServiceBinding(StrictSpec):
    """Select a trusted implementation and immutable release-local references."""

    implementation: Name
    model: Name | None = None
    prompts: dict[Name, Name] = Field(default_factory=dict, max_length=32)


class NodeSpec(StrictSpec):
    id: Name
    kind: Name
    needs: list[Name] = Field(max_length=128)
    bindings: dict[Name, ServiceBinding] = Field(default_factory=dict, max_length=32)


class EdgeSpec(StrictSpec):
    source: Endpoint = Field(alias="from")
    target: Endpoint = Field(alias="to")


class RouteSpec(StrictSpec):
    source: Name = Field(alias="from")
    router: Name
    paths: dict[Name, Endpoint] = Field(min_length=1, max_length=128)


class WorkflowSpec(StrictSpec):
    schema_version: Literal[1]
    name: Name
    nodes: list[NodeSpec] = Field(min_length=1, max_length=128)
    edges: list[EdgeSpec] = Field(min_length=1, max_length=256)
    routes: list[RouteSpec] = Field(default_factory=list, max_length=128)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def parse_workflow_spec(value: str | dict | WorkflowSpec) -> WorkflowSpec:
    """Parse JSON text or database data; never interpret a string as a file path."""
    try:
        if isinstance(value, WorkflowSpec):
            # Frozen models can still contain mutable lists. Validate a fresh snapshot.
            value = value.model_dump(by_alias=True)
        if isinstance(value, str):
            return WorkflowSpec.model_validate_json(value)
        return WorkflowSpec.model_validate(value)
    except ValidationError as exc:
        raise WorkflowConfigurationError(f"Invalid workflow definition: {exc}") from exc


def load_workflow_spec(path: str | Path) -> WorkflowSpec:
    """Read a server-selected configuration file, outside the HTTP chat handler."""
    try:
        contents = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise WorkflowConfigurationError(f"Cannot read workflow definition {path}: {exc}") from exc
    return parse_workflow_spec(contents)
