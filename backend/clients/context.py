"""Immutable server-resolved identity, independent of a business domain."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


ClientId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
NonEmptyText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class ClientContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: ClientId
    display_name: NonEmptyText
    # This iteration has no release database. The current deployment is legacy.
    release_id: NonEmptyText = "legacy"
