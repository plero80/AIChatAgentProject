"""Task inputs, independent of HTTP and command-line argument parsing."""

from dataclasses import dataclass

from backend.tenancy import validate_dietitian_id


@dataclass(frozen=True)
class ChatGraphRequest:
    message: str
    user_id: str
    dietitian_id: str

    def __post_init__(self):
        validate_dietitian_id(self.dietitian_id)

    @property
    def thread_id(self) -> str:
        return f"{self.dietitian_id}:{self.user_id}"


@dataclass(frozen=True)
class RecipeIngestionRequest:
    raw_recipe: str
    dietitian_id: str

    def __post_init__(self):
        validate_dietitian_id(self.dietitian_id)
