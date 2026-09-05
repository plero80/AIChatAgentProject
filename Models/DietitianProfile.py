from pydantic import BaseModel, Field
from typing import Literal


class DietitianProfile(BaseModel):
    name: str = Field()
    age: int = Field()
    gender: Literal["male", "female", "other"] = Field()
    goals: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    medical_conditions: list[str] = Field(default_factory=list)



    def is_complete(self) -> bool:
        return all(self.goals, self.restrictions, self.medical_conditions)

    def to_dict(self) -> dict:
        return self.model_dump()