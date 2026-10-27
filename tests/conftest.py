import sys
from pathlib import Path

# Make the backend package importable when pytest is launched as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def isolate_deployment_choice(monkeypatch):
    """Offline tests must not inherit an operator's deployment selection."""
    monkeypatch.setenv("DIETITIAN_ID", "clinic_001")
    monkeypatch.setenv("CLIENT_CONFIG_SOURCE", "file")
    monkeypatch.delenv("IMAGE_RECOGNITION_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def isolate_graph_build_trace(monkeypatch, tmp_path):
    """Tests must not append builds to an operator's application trace file."""
    monkeypatch.setenv("GRAPH_BUILD_LOG", str(tmp_path / "graph_build.jsonl"))


@pytest.fixture(autouse=True)
def offline_admission(monkeypatch):
    """Replace only the guard's SDK transport; real admission/wiring still run."""
    import json
    from types import SimpleNamespace
    import httpx
    from openai import AsyncOpenAI
    from backend.guards import decisions

    state = SimpleNamespace(calls=[], clients=[], handler=None)

    def default_handler(request):
        assert request.url.path == "/v1/decisions"
        state.calls.append(json.loads(request.content))
        if state.handler is not None:
            return state.handler(request)
        return httpx.Response(200, json={"answers": [
            {"type": "predicate", "name": "unsafe_request", "probability": 0.0},
            {"type": "predicate", "name": "in_support_scope", "probability": 1.0},
        ]})

    def sdk(**kwargs):
        client = AsyncOpenAI(
            api_key="offline-test-key", base_url="https://guard.test/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(default_handler)),
            **kwargs,
        )
        state.clients.append(client)
        return client

    monkeypatch.setattr(decisions, "AsyncOpenAI", sdk)
    monkeypatch.delenv("GUARD_CONFIG", raising=False)
    return state


@pytest.fixture
def release_document():
    """Real public document shape; only the provider is changed to a local fake."""
    import json
    from backend.clients.releases import ReleaseSpec
    from backend.paths import CONFIG_DIR

    def make(client_id="chef_demo", *, version=1, model="writer-one"):
        data = json.loads((CONFIG_DIR / "releases" / "chef.example.json").read_text(encoding="utf-8"))
        data["client_id"] = client_id
        data["models"]["writer"] = {"provider": "local", "model": model, "max_tokens": 100}
        data["prompts"]["chef_support"] = {"version": version, "text": f"Published instructions v{version} for $display_name"}
        return ReleaseSpec.model_validate(data)

    return make


@pytest.fixture
def release_factories():
    from types import SimpleNamespace
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from backend.factories.service_factories import ServiceFactories

    state = SimpleNamespace(factories=ServiceFactories(), models=[], closed=[], calls=[])

    class LocalModel(BaseChatModel):
        label: str

        @property
        def _llm_type(self):
            return "local-release-test"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            state.calls.append((self.label, messages))
            turns = sum(isinstance(message, HumanMessage) for message in messages)
            text = f"{self.label}: turn {turns}"
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def build(*, profile, resources):
        resources.callback(state.closed.append, profile.model)
        if profile.model == "broken":
            raise RuntimeError("Construction failed")
        value = LocalModel(label=profile.model)
        state.models.append(value)
        return value

    state.factories.chat_model.register("local", build, uses_resources=True)
    return state


@pytest.fixture
def photo_payload():
    import base64
    from io import BytesIO
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (24, 16), (200, 80, 50)).save(output, format="PNG")
    return {"mime_type": "image/png", "data": base64.b64encode(output.getvalue()).decode("ascii")}


@pytest.fixture
def photo_payloads():
    """Ten distinct images so tests detect dropped, duplicated, or reordered inputs."""
    import base64
    from io import BytesIO
    from PIL import Image

    photos = []
    for index in range(10):
        output = BytesIO()
        Image.new("RGB", (24, 16), (index * 20, 80, 200 - index * 15)).save(output, format="PNG")
        photos.append({"mime_type": "image/png", "data": base64.b64encode(output.getvalue()).decode("ascii")})
    return photos
