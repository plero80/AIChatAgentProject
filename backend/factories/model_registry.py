"""Build assigned LLM clients once at startup and reuse them by profile name."""

from collections.abc import Mapping
from types import MappingProxyType

from langchain_core.language_models.chat_models import BaseChatModel

from backend.factories.service_factory import ServiceFactory
from backend.config.models import ModelConfig
from backend.config.services import ServiceConfigurationError


class ModelRegistry:
    def __init__(self, config: ModelConfig, factory: ServiceFactory[BaseChatModel]):
        self.validate(config, factory)
        self.config = config
        # Multiple roles referring to one profile share exactly one client.
        # Unassigned profiles are available for experiments but are not constructed.
        names = dict.fromkeys(config.assignments.model_dump().values())
        self.models: Mapping[str, BaseChatModel] = MappingProxyType({
            name: factory.create(config.models[name].provider, profile=config.models[name])
            for name in names
        })

    @staticmethod
    def validate(config: ModelConfig, factory: ServiceFactory[BaseChatModel]) -> None:
        """Check all profile providers without constructing clients or opening connections."""
        for name, profile in config.models.items():
            try:
                factory.validate(profile.provider)
            except ServiceConfigurationError as exc:
                raise ServiceConfigurationError(f"Model profile {name!r}: {exc}") from exc

    def for_role(self, role: str) -> BaseChatModel:
        return self.models[getattr(self.config.assignments, role)]
