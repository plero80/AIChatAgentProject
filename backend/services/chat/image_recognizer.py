"""Extract bounded observations from photos; never execute instructions inside them."""

from abc import ABC, abstractmethod
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.models.chat_input import ChatImage, MAX_IMAGES


IngredientName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]


class ImageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")
    ingredients: list[IngredientName] = Field(max_length=40)
    uncertain_items: list[IngredientName] = Field(max_length=20)
    description: str = Field(min_length=1, max_length=1000)


class BaseImageRecognizer(ABC):
    @abstractmethod
    async def recognize(self, images: tuple[ChatImage, ...], message: str) -> ImageObservation:
        """Describe image evidence without claiming certainty about hidden properties."""


class LLMImageRecognizer(BaseImageRecognizer):
    def __init__(self, *, llm, prompt):
        self.llm = llm
        self.prompt = prompt

    async def recognize(self, images, message):
        if not 1 <= len(images) <= MAX_IMAGES:
            raise ValueError("Recognition requires between one and ten photos")
        result = await self.llm.with_structured_output(ImageObservation).ainvoke([
            SystemMessage(content=self.prompt + "\nConsider all attached photos together. Combine distinct ingredient "
                          "names across the photos and avoid duplicates from repeated views of the same ingredient. "
                          "Do not infer quantities by counting repeated views. Keep uncertain items separate."),
            HumanMessage(content=[
                {"type": "text", "text": message or "Identify the visible cooking ingredients in these photos."},
                *[{"type": "image_url", "image_url": {"url": image.data_url}} for image in images],
            ]),
        ])
        return ImageObservation.model_validate(result)
