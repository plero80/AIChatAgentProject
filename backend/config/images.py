"""Versionable ingredient-recognition configuration, independent of graph topology."""

import os
from pathlib import Path

from pydantic import Field

from backend.config.models import ModelProfile
from backend.config.workflows import Name, StrictSpec
from backend.paths import CONFIG_DIR


class ImageRecognitionConfig(StrictSpec):
    implementation: Name = "llm"
    model: ModelProfile
    prompt: str = Field(min_length=1, max_length=16000)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120, allow_inf_nan=False)


def load_image_config(path=None):
    path = Path(path or os.getenv("IMAGE_RECOGNITION_CONFIG", CONFIG_DIR / "image_recognition.json"))
    return ImageRecognitionConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))


def validate_image_config(config, factories):
    if config is not None:
        factories.image_recognizer.validate(config.implementation)
        factories.chat_model.validate(config.model.provider)
