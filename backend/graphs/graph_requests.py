"""Task inputs, independent of HTTP and command-line argument parsing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatGraphRequest:
    message: str
    user_id: str


@dataclass(frozen=True)
class RecipeIngestionRequest:
    raw_recipe: str
