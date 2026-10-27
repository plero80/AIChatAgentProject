"""Chat provider construction; callers use BaseChatModel."""

from contextlib import AsyncExitStack

from langchain_core.language_models.chat_models import BaseChatModel

from backend.config.models import ModelProfile
from backend.factories.provider_resources import openai_transport_options, own_http_clients


def openai_chat(*, profile: ModelProfile, resources: AsyncExitStack | None = None) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    options = openai_transport_options(resources)
    if profile.reasoning_effort is not None:
        options["reasoning_effort"] = profile.reasoning_effort
    return ChatOpenAI(
        model=profile.model,
        max_tokens=profile.max_tokens,
        **options,
    )


def anthropic_chat(*, profile: ModelProfile, resources: AsyncExitStack | None = None) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    model = ChatAnthropic(model=profile.model, max_tokens=profile.max_tokens)
    if resources is not None:
        from anthropic import Client, AsyncClient, DefaultHttpxClient, DefaultAsyncHttpxClient

        sync, asynchronous = own_http_clients(
            resources, DefaultHttpxClient, DefaultAsyncHttpxClient,
            proxy=model.anthropic_proxy or None,
        )
        # This adapter exposes SDK clients through cached properties. Seed those
        # slots with owned transports; its defaults may share HTTP clients across
        # models, which must not be closed on eviction of just one workflow.
        model.__dict__["_client"] = Client(**model._client_params, http_client=sync)
        model.__dict__["_async_client"] = AsyncClient(**model._client_params, http_client=asynchronous)
    return model


def google_chat(*, profile: ModelProfile, resources: AsyncExitStack | None = None) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    model = ChatGoogleGenerativeAI(model=profile.model, max_tokens=profile.max_tokens)
    if resources is not None:
        resources.push_async_callback(model.aclose)
    return model
