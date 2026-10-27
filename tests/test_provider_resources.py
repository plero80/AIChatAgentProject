"""Owned provider clients close independently, without live API calls."""

import asyncio
from contextlib import AsyncExitStack

import pytest

from backend.config.models import ModelProfile
from backend.factories.service_factories import ServiceFactories
from backend.factories import provider_resources


@pytest.fixture
def credentials(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(key, "offline-test-key")
    for key in ("OPENAI_PROXY", "ANTHROPIC_PROXY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")


def is_closed(model, provider):
    if provider == "openai":
        return model.http_client.is_closed and model.http_async_client.is_closed
    if provider == "anthropic":
        return model._client.is_closed() and model._async_client.is_closed()
    return model._client_cleanup._closed


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google_genai"])
def test_owned_models_close_without_closing_another_runtime(credentials, provider):
    async def run():
        first, second = AsyncExitStack(), AsyncExitStack()
        profile = ModelProfile(provider=provider, model="offline-model")
        factory = ServiceFactories().chat_model
        try:
            a = factory.create(provider, profile=profile, resources=first)
            b = factory.create(provider, profile=profile, resources=second)
            assert not is_closed(a, provider)
            assert not is_closed(b, provider)
            await first.aclose()
            assert is_closed(a, provider)
            assert not is_closed(b, provider)
        finally:
            await first.aclose()
            await second.aclose()
        assert is_closed(b, provider)
    asyncio.run(run())


def test_provider_constructor_failure_closes_preallocated_transports(credentials, monkeypatch):
    import langchain_openai

    clients = []
    original = provider_resources.own_http_clients

    def track(*args, **kwargs):
        pair = original(*args, **kwargs)
        clients.extend(pair)
        return pair

    def broken(**kwargs):
        raise RuntimeError("constructor failed")

    monkeypatch.setattr(provider_resources, "own_http_clients", track)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", broken)

    async def run():
        with pytest.raises(RuntimeError, match="constructor failed"):
            async with AsyncExitStack() as resources:
                ServiceFactories().chat_model.create(
                    "openai", profile=ModelProfile(provider="openai", model="offline-model"), resources=resources,
                )
        assert len(clients) == 2
        assert all(client.is_closed for client in clients)
    asyncio.run(run())
