"""Text and up to ten uploaded photos. Uploads never contain remote URLs."""

import base64
import binascii
from io import BytesIO
from typing import Annotated, Literal
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, model_validator


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BASE64 = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGES = 10
MAX_CHAT_BODY_BYTES = MAX_IMAGES * MAX_IMAGE_BASE64 + 256 * 1024
IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp")


class ChatImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: str = Field(min_length=1, max_length=MAX_IMAGE_BASE64, repr=False)
    _validated: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def validate_upload(self):
        if self._validated:
            return self
        try:
            raw = base64.b64decode(self.data, validate=True)
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Photo must be at most 5 MB")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as source:
                    actual_type = Image.MIME.get(source.format)
                    if actual_type != self.mime_type or actual_type not in IMAGE_MIME_TYPES:
                        raise ValueError("Photo contents do not match the declared image type")
                    if source.width * source.height > MAX_IMAGE_PIXELS:
                        raise ValueError("Photo must be at most 20 megapixels")
                    if getattr(source, "n_frames", 1) != 1:
                        raise ValueError("Please send a still photo")
                    source.verify()
            self._validated = True
        except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError,
                Image.DecompressionBombWarning):
            raise ValueError("Please upload a valid JPEG, PNG or WebP photo") from None
        return self

    @property
    def data_url(self):
        return f"data:{self.mime_type};base64,{self.data}"


class InvalidChatImage(ValueError):
    pass


def normalize_chat_image(image: ChatImage) -> ChatImage:
    """Called in a worker thread, after rate limiting and before guard/model calls."""
    try:
        with Image.open(BytesIO(base64.b64decode(image.data, validate=True))) as source:
            source.load()  # JPEG verify alone cannot detect all truncated pixel data.
            oriented = ImageOps.exif_transpose(source)
            oriented.thumbnail((1536, 1536))
            pixels = oriented.convert("RGBA")
            clean = Image.new("RGB", pixels.size, "white")
            clean.paste(pixels, mask=pixels.getchannel("A"))
            output = BytesIO()
            clean.save(output, format="JPEG", quality=85)
        return ChatImage(mime_type="image/jpeg", data=base64.b64encode(output.getvalue()).decode("ascii"))
    except (ValueError, OSError, Image.DecompressionBombError):
        raise InvalidChatImage("Please upload a valid JPEG, PNG or WebP photo") from None


def normalize_chat_images(images: tuple[ChatImage, ...]) -> tuple[ChatImage, ...]:
    # Decode sequentially inside one worker, rather than holding ten full-size
    # decoded photos in memory at once. Preserve the user's attachment order.
    return tuple(normalize_chat_image(image) for image in images)


class ChatContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    message: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=16000)] = ""
    images: tuple[ChatImage, ...] = Field(default=(), max_length=MAX_IMAGES, repr=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_attachments(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            # Older frontends may still send a single `image` during rollout.
            if "image" in value:
                if "images" in value:
                    raise ValueError("Send images or the legacy image field, not both")
                image = value.pop("image")
                value["images"] = [image] if image is not None else []
            images = value.get("images", ())
            if isinstance(images, (list, tuple)) and len(images) > MAX_IMAGES:
                # Reject before decoding any image headers.
                raise ValueError(f"Send at most {MAX_IMAGES} photos per message")
        return value

    @model_validator(mode="after")
    def has_content(self):
        if not self.message and not self.images:
            raise ValueError("Send text, photos, or both")
        return self


def image_upload_capabilities(enabled: bool):
    return {"enabled": enabled, "max_count": MAX_IMAGES, "max_bytes": MAX_IMAGE_BYTES,
            "max_total_bytes": MAX_IMAGES * MAX_IMAGE_BYTES, "mime_types": list(IMAGE_MIME_TYPES)}
