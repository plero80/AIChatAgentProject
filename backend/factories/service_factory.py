"""A separate typed registry is created for each service contract."""

import inspect
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Generic, TypeVar

from backend.config.services import ServiceConfigurationError


T = TypeVar("T")


class ServiceFactory(Generic[T]):
    def __init__(self, name: str, contract: type[T]):
        self.name = name
        self.contract = contract
        self._builders: dict[str, Callable[..., T]] = {}
        self._resource_builders: set[str] = set()

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def register(self, name: str, builder: Callable[..., T], *, uses_resources: bool = False) -> None:
        """Resource-aware builders also accept resources= and register owned cleanup."""
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
        if uses_resources:
            self._resource_builders.add(name)

    def validate(self, name: str) -> None:
        if name not in self._builders:
            raise ServiceConfigurationError(
                f"Unknown {self.name} implementation {name!r}. "
                f"Available: {', '.join(self.available)}"
            )

    def create(self, name: str, *, resources: AsyncExitStack | None = None, **dependencies) -> T:
        self.validate(name)
        if name in self._resource_builders:
            dependencies["resources"] = resources
        service = self._builders[name](**dependencies)
        if not isinstance(service, self.contract):
            raise TypeError(f"{self.name} builder must return {self.contract.__name__}")
        return service
