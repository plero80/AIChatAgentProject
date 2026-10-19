"""Chat provider construction; callers use BaseChatModel."""

from langchain_core.language_models.chat_models import BaseChatModel

from backend.config.models import ModelProfile


def openai_chat(*, profile: ModelProfile) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    options = {}
    if profile.reasoning_effort is not None:
        options["reasoning_effort"] = profile.reasoning_effort
    return ChatOpenAI(
        model=profile.model,
        max_tokens=profile.max_tokens,
        **options,
    )


def anthropic_chat(*, profile: ModelProfile) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=profile.model, max_tokens=profile.max_tokens)


def google_chat(*, profile: ModelProfile) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=profile.model, max_tokens=profile.max_tokens)
