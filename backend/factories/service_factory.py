"""A separate typed registry is created for each service contract."""

import inspect
import re
from collections.abc import Callable
from typing import Generic, TypeVar

from backend.config.services import ServiceConfigurationError


T = TypeVar("T")


class ServiceFactory(Generic[T]):
    def __init__(self, name: str, contract: type[T]):
        self.name = name
        self.contract = contract
        self._builders: dict[str, Callable[..., T]] = {}

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def register(self, name: str, builder: Callable[..., T]) -> None:
        """Register a concrete class or an adapter accepting the service's dependencies."""
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"Invalid implementation name: {name!r}")
        if name in self._builders:
            raise ValueError(f"{self.name} implementation {name!r} is already registered")
        if not callable(builder):
            raise TypeError(f"{self.name} builder must be callable")
        if inspect.isclass(builder):
            if not issubclass(builder, self.contract) or inspect.isabstract(builder):
                raise TypeError(f"{self.name} must implement {self.contract.__name__}")
        self._builders[name] = builder

    def validate(self, name: str) -> None:
        if name not in self._builders:
            raise ServiceConfigurationError(
                f"Unknown {self.name} implementation {name!r}. "
                f"Available: {', '.join(self.available)}"
            )

    def create(self, name: str, **dependencies) -> T:
        self.validate(name)
        service = self._builders[name](**dependencies)
        if not isinstance(service, self.contract):
            raise TypeError(f"{self.name} builder must return {self.contract.__name__}")
        return service
