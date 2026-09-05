from pydantic import BaseModel, Field
from typing import Literal


class RecipePlan(BaseModel):
    ingredients: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    mode: Literal["existing", "inspired", "original"] = Field()


    def to_dict(self) -> dict:
        return self.model_dump()