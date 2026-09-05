from pydantic import BaseModel, Field
from typing import Literal


class MessageAnalysis(BaseModel):
    intent: Literal["chat", "food", "dietitian_escort"]

    recipe_mode: Literal[
        "existing",
        "inspired",
        "original",
        "none"
    ]

    ingredients: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)


    def is_food_request(self) -> bool:
        return self.intent == "food"


    
    def is_dietitian_escorts_request(self) -> bool:
        return self.intent == "dietitian_escort"

   