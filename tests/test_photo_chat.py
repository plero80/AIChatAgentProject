import asyncio
import base64
from contextlib import AsyncExitStack
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from PIL import Image
from pydantic import ValidationError
import pytest

from backend.config.images import ImageRecognitionConfig
from backend.models.chat_input import ChatContent, ChatImage, normalize_chat_image
from backend.services.chat.image_recognizer import BaseImageRecognizer, ImageObservation, LLMImageRecognizer
from backend.workflows.contracts import ConversationRequest
from backend.workflows.image_input import ImageInputProcessor, ImageRecognitionUnavailable
import backend.web.api as api


def vision_config():
    return ImageRecognitionConfig(
        implementation="fake", model={"provider": "local", "model": "vision-test", "max_tokens": 300},
        prompt="Identify visible ingredients only", timeout_seconds=1.0,
    )


@pytest.mark.parametrize("text,attached", [("Salty recipe", False), ("", True), ("Salty recipe", True)])
def test_accepts_text_photo_or_both(photo_payload, text, attached):
    content = ChatContent(message=text, image=photo_payload if attached else None)
    assert content.message == text
    assert bool(content.images) == attached


@pytest.mark.parametrize("payload", [{}, {"message": " "}, {"message": None}, {"image": None}, {"images": []}, {"images": None}])
def test_empty_request_is_rejected(payload):
    with pytest.raises(ValidationError):
        ChatContent.model_validate(payload)


@pytest.mark.parametrize("changes", [
    {"mime_type": "image/svg+xml"}, {"mime_type": "image/jpeg"}, {"data": "not base64!"},
    {"data": "https://example.com/photo.png"}, {"data": ""}, {"url": "http://localhost/private"},
    {"data": base64.b64encode(b"not an image").decode()},
])
def test_uploads_must_be_actual_supported_images(photo_payload, changes):
    with pytest.raises(ValidationError):
        ChatImage.model_validate({**photo_payload, **changes})


def test_normalization_removes_metadata_and_corrects_orientation():
    source = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private image metadata"
    raw = BytesIO()
    source.save(raw, format="JPEG", exif=exif)
    upload = ChatImage(mime_type="image/jpeg", data=base64.b64encode(raw.getvalue()).decode())
    clean = normalize_chat_image(upload)
    with Image.open(BytesIO(base64.b64decode(clean.data))) as image:
        assert image.size == (20, 40)
        assert not image.getexif()
    assert b"private image metadata" not in base64.b64decode(clean.data)
    assert clean.data not in repr(clean)


def test_limits_and_animation_rejected(photo_payload, monkeypatch):
    import backend.models.chat_input as inputs
    monkeypatch.setattr(inputs, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(ValidationError, match="megapixels"):
        ChatImage.model_validate(photo_payload)
    monkeypatch.setattr(inputs, "MAX_IMAGE_PIXELS", 20_000_000)
    monkeypatch.setattr(inputs, "MAX_IMAGE_BYTES", 1)
    with pytest.raises(ValidationError, match="5 MB"):
        ChatImage.model_validate(photo_payload)
    monkeypatch.setattr(inputs, "MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    raw = BytesIO()
    Image.new("RGB", (2, 2), "red").save(raw, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (2, 2), "blue")], duration=100, loop=0)
    with pytest.raises(ValidationError, match="still photo"):
        ChatImage(mime_type="image/png", data=base64.b64encode(raw.getvalue()).decode())


@pytest.mark.parametrize("count", [1, 10])
def test_real_openai_adapter_serializes_photos_and_structured_output(photo_payloads, count):
    from langchain_openai import ChatOpenAI
    captured = []

    def provider(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({
                "ingredients": ["tomato", "cucumber"], "uncertain_items": ["herbs"], "description": "Vegetables on a table",
            })}}],
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with httpx.Client(transport=httpx.MockTransport(provider)) as synchronous:
                model = ChatOpenAI(model="gpt-5.4-mini", api_key="offline", base_url="https://vision.test/v1",
                                   http_async_client=client, http_client=synchronous, max_retries=0)
                service = LLMImageRecognizer(llm=model, prompt="Trusted extraction instructions")
                images = tuple(ChatImage.model_validate(photo) for photo in photo_payloads[:count])
                observation = await service.recognize(images, "Something salty")
                assert observation.ingredients == ["tomato", "cucumber"]
        content = captured[0]["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": "Something salty"}
        assert len(captured) == 1
        assert [part["image_url"]["url"] for part in content[1:]] == [image.data_url for image in images]
        assert captured[0]["response_format"]["type"] == "json_schema"
    asyncio.run(scenario())


@pytest.fixture
def photo_api(monkeypatch, release_factories):
    from backend.graphs.recipe_chat_graph import RecipeChatGraph
    from backend.nodes.chat.recipe_node import RecipeNode
    from backend.services.contracts import BaseRecipeGenerationService, BaseRecipeSearchService
    from backend.models.message_analysis import MessageAnalysis
    from backend.workflows.checkpoint_types import checkpoint_serializer

    state = SimpleNamespace(recognized=[], analyzed=[], plans=[], failure=None)
    memory = InMemorySaver(serde=checkpoint_serializer())
    memory.aput = AsyncMock(wraps=memory.aput)
    memory.aput_writes = AsyncMock(wraps=memory.aput_writes)

    class Pool:
        check_connection = None
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    class Recognizer(BaseImageRecognizer):
        async def recognize(self, images, message):
            state.recognized.append((images, message))
            if state.failure == "timeout":
                await asyncio.Event().wait()
            if state.failure:
                raise RuntimeError("Provider failed")
            return ImageObservation(ingredients=["tomato", "cucumber"], uncertain_items=[], description="Two vegetables")

    class Generation(BaseRecipeGenerationService):
        async def generate_original_recipe(self, plan):
            state.plans.append(plan)
            return "A salty tomato and cucumber salad"
        async def generate_inspired_recipe(self, plan, similar_recipes):
            raise AssertionError("Unexpected branch")

    class Search(BaseRecipeSearchService):
        async def find_best_recipes(self, user_text, plan, top_k=5):
            raise AssertionError("Unexpected branch")

    def graph_factory(*, tenant, checkpointer, **kwargs):
        def analyze(data):
            state.analyzed.append(data["message"])
            return {"analysis": MessageAnalysis(intent="food", recipe_mode="original",
                ingredients=[name for name in ("tomato", "cucumber") if name in data["message"]],
                preferences=["salty"] if "salty" in data["message"] else [])}

        def respond(data):
            text = data["result"]["text"]
            return {"final_response": text, "response_blocks": [{"type": "text", "text": text}],
                    "messages": [AIMessage(content=text)]}

        nodes = SimpleNamespace(analysis=analyze, recipe=RecipeNode(Generation(), Search(), tenant=tenant),
                                chat=lambda _: {}, dietitian=lambda _: {}, response=respond)
        return RecipeChatGraph(nodes, tenant=tenant, checkpointer=checkpointer)

    release_factories.factories.image_recognizer.register("fake", lambda **kwargs: Recognizer())
    monkeypatch.setattr(api, "ServiceFactories", lambda: release_factories.factories)
    monkeypatch.setattr(api, "load_image_config", vision_config)
    monkeypatch.setattr(api, "AsyncConnectionPool", Pool)
    memory.setup = AsyncMock()
    monkeypatch.setattr(api, "AsyncPostgresSaver", lambda _: memory)
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)
    state.app = api.create_http_app(graph_factory=graph_factory)
    state.memory = memory
    return state


@pytest.mark.parametrize("message", ["salty recipe", ""])
@pytest.mark.parametrize("count,legacy", [(1, True), (1, False), (10, False)])
def test_api_photo_continues_recipe_flow_and_only_text_reaches_checkpoints(photo_api, photo_payloads, offline_admission, message, count, legacy):
    photos = photo_payloads[:count]
    with TestClient(photo_api.app) as browser:
        capability = browser.get("/public-config").json()["image_upload"]
        assert capability["enabled"] and capability["max_count"] == 10
        assert capability["max_total_bytes"] == 10 * capability["max_bytes"]
        attachments = {"image": photos[0]} if legacy else {"images": photos}
        response = browser.post("/chat", json={"message": message, **attachments})
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "A salty tomato and cucumber salad"
        assert photo_api.plans[0].ingredients == ["tomato", "cucumber"]
        assert photo_api.plans[0].preferences == (["salty"] if message else [])
        assert message in photo_api.analyzed[0]
        assert len(photo_api.recognized) == 1
        recognized_images, recognized_message = photo_api.recognized[0]
        assert recognized_message == message
        expected_images = tuple(normalize_chat_image(ChatImage.model_validate(photo)) for photo in photos)
        assert recognized_images == expected_images
        assert len(offline_admission.calls) == 1
        decision_input = offline_admission.calls[0]["input"][0]["content"]
        assert [part["image_url"] for part in decision_input[1:]] == [image.data_url for image in expected_images]
        assert json.loads(decision_input[0]["text"])["photo_count"] == count
        thread = "clinic_001:" + browser.cookies.get("alona_session")
        saved = photo_api.memory.get_tuple({"configurable": {"thread_id": thread}})
        rendered = str(saved.checkpoint)
        assert "tomato" in rendered and "data:image" not in rendered
        assert all(photo["data"] not in rendered for photo in photos)
        assert all(image.data not in rendered for image in recognized_images)
        previous_calls = len(photo_api.recognized)
        assert browser.post("/chat", json={"message": "Another idea"}).status_code == 200
        assert len(photo_api.recognized) == previous_calls
        evidence = json.loads(offline_admission.calls[-1]["input"])
        assert "tomato" in evidence["history"]


def test_unsafe_photo_is_rejected_before_recognition_or_checkpoint(photo_api, photo_payloads, offline_admission):
    offline_admission.handler = lambda req: httpx.Response(200, json={"answers": [
        {"type": "predicate", "name": "unsafe_request", "probability": 1.0},
        {"type": "predicate", "name": "in_support_scope", "probability": 1.0},
    ]})
    with TestClient(photo_api.app) as browser:
        assert browser.post("/chat", json={"images": photo_payloads}).status_code == 200
        assert len(offline_admission.calls[0]["input"][0]["content"]) == 11
        assert not photo_api.recognized and not photo_api.analyzed
        thread = "clinic_001:" + browser.cookies.get("alona_session")
        assert photo_api.memory.get_tuple({"configurable": {"thread_id": thread}}) is None


@pytest.mark.parametrize("case", ["eleven", "invalid_last", "mixed_fields"])
def test_invalid_batches_rejected_without_partial_processing(photo_api, photo_payloads, offline_admission, case):
    payload = {"images": photo_payloads}
    if case == "eleven":
        payload["images"] = photo_payloads + [photo_payloads[0]]
    elif case == "invalid_last":
        payload["images"] = photo_payloads[:-1] + [{**photo_payloads[-1], "mime_type": "image/jpeg"}]
    else:
        payload["image"] = photo_payloads[0]
    with TestClient(photo_api.app) as browser:
        response = browser.post("/chat", json=payload)
        assert response.status_code == 422
        assert all(photo["data"] not in response.text for photo in photo_payloads)
        assert not offline_admission.calls and not photo_api.recognized and not photo_api.analyzed
        photo_api.memory.aput.assert_not_called()
        photo_api.memory.aput_writes.assert_not_called()


def test_count_limit_checked_before_decoding_images(monkeypatch):
    def unexpected_decode(*args, **kwargs):
        raise AssertionError("Must reject attachment count before opening photos")
    monkeypatch.setattr("backend.models.chat_input.Image.open", unexpected_decode)
    with pytest.raises(ValidationError, match="at most 10"):
        ChatContent(images=[{"mime_type": "image/png", "data": "aW1hZ2U="}] * 11)


def test_chat_body_budget_accepts_ten_max_size_attachments():
    from backend.models.chat_input import MAX_CHAT_BODY_BYTES, MAX_IMAGE_BASE64
    # Account for encoded image data, UTF-8 text, and JSON framing.
    photo = {"mime_type": "image/png", "data": "x" * MAX_IMAGE_BASE64}
    body = json.dumps({"message": "\U0001f373" * 16000, "images": [photo] * 10})
    assert len(body.encode()) <= MAX_CHAT_BODY_BYTES


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_recognition_failure_does_not_advance_graph(photo_api, photo_payload, failure):
    photo_api.failure = failure
    with TestClient(photo_api.app) as browser:
        result = browser.post("/chat", json={"message": "salty", "image": photo_payload})
        assert result.status_code == 503
        assert not photo_api.analyzed
        assert not photo_api.memory.aput.called
        assert not photo_api.memory.aput_writes.called


def test_invalid_and_oversized_requests_do_not_echo_base64_or_call_guard(photo_api, photo_payload, offline_admission, monkeypatch):
    with TestClient(photo_api.app) as browser:
        result = browser.post("/chat", json={"image": {**photo_payload, "mime_type": "image/jpeg"}})
        assert result.status_code == 422
        assert photo_payload["data"] not in result.text
        assert "input" not in result.json()["detail"][0]
        monkeypatch.setattr("backend.web.body_limit.MAX_CHAT_BODY_BYTES", 10)
        assert browser.post("/chat", content=b"x" * 11).status_code == 413
        assert browser.post("/api/clients/chef/chat", content=iter([b"x" * 6, b"y" * 6])).status_code == 413
        assert not offline_admission.calls


def test_failed_lazy_model_build_cleans_resources(photo_payload, release_factories):
    async def scenario():
        config = vision_config().model_copy(update={"model": vision_config().model.model_copy(update={"model": "broken"})})
        async with AsyncExitStack() as resources:
            processor = ImageInputProcessor(config, release_factories.factories, resources)
            with pytest.raises(ImageRecognitionUnavailable):
                await processor(ConversationRequest(client_id="chef", conversation_id="user", image=photo_payload))
            assert release_factories.closed == ["broken"]
    asyncio.run(scenario())


def test_truncated_jpeg_rejected_before_guard(photo_api, offline_admission):
    raw = BytesIO()
    Image.new("RGB", (32, 32), "red").save(raw, format="JPEG")
    image = {"mime_type": "image/jpeg", "data": base64.b64encode(raw.getvalue()[:-2]).decode()}
    with TestClient(photo_api.app) as browser:
        result = browser.post("/chat", json={"image": image})
        assert result.status_code == 422
        assert not offline_admission.calls


def test_adding_optional_photo_configuration_preserves_old_release_fingerprints(release_document):
    import hashlib
    from backend.clients.releases import ReleaseSpec
    # Simulate the exact JSON stored before the image capability existed.
    document = release_document().model_dump(mode="json", by_alias=True)
    document.pop("image_recognition", None)
    old_fingerprint = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    restored = ReleaseSpec.model_validate(document)
    assert restored.image_recognition is None
    assert restored.fingerprint() == old_fingerprint
    assert "image_recognition" not in restored.model_dump(mode="json")
