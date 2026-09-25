from pydantic import BaseModel, Field


class RequestValidation(BaseModel):
    legitimate: bool
    reason: str = Field(
        default="",
        description="User-facing refusal when legitimate is false. Empty when legitimate is true.",
    )
