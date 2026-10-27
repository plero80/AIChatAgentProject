"""Trusted Python contracts and factories; JSON can only select registered names."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any

from pydantic import TypeAdapter

from backend.config.workflows import NodeSpec, WorkflowConfigurationError


Node = Callable[[Mapping[str, Any]], dict | Awaitable[dict]]


@dataclass(frozen=True)
class StateField:
    annotation: Any
    reducer: Callable | None = None

    def __post_init__(self):
        TypeAdapter(self.annotation)
        if self.reducer is not None and not callable(self.reducer):
            raise TypeError("A state reducer must be callable")


def _fields(values: Mapping[str, StateField]) -> Mapping[str, StateField]:
    for name, definition in values.items():
        _name(name)
        if not isinstance(definition, StateField):
            raise TypeError(f"Field {name!r} must have a StateField contract")
    return MappingProxyType(dict(values))


def _name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise ValueError(f"Invalid registered name: {value!r}")


@dataclass(frozen=True)
class NodeDefinition:
    factory: Callable[[], Node]
    inputs: Mapping[str, StateField]
    outputs: Mapping[str, StateField]
    # Allowed updates which are not guaranteed after the node executes.
    optional_outputs: frozenset[str] = field(default_factory=frozenset)
    # Opt-in binding support, implemented in trusted Python, never imported by JSON.
    binding_factory: Callable[[NodeSpec], Node] | None = None

    def __post_init__(self):
        if not callable(self.factory):
            raise TypeError("A node factory must be callable")
        if self.binding_factory is not None and not callable(self.binding_factory):
            raise TypeError("A binding factory must be callable")
        object.__setattr__(self, "inputs", _fields(self.inputs))
        object.__setattr__(self, "outputs", _fields(self.outputs))
        object.__setattr__(self, "optional_outputs", frozenset(self.optional_outputs))
        if not self.optional_outputs <= self.outputs.keys():
            raise ValueError("optional_outputs must refer to declared outputs")


@dataclass(frozen=True)
class RouterDefinition:
    route: Callable[[Mapping[str, Any]], str]
    inputs: Mapping[str, StateField]
    outcomes: frozenset[str]

    def __post_init__(self):
        if not callable(self.route):
            raise TypeError("A router must be callable")
        object.__setattr__(self, "inputs", _fields(self.inputs))
        object.__setattr__(self, "outcomes", frozenset(self.outcomes))
        if not self.outcomes:
            raise ValueError("A router must declare at least one outcome")
        for outcome in self.outcomes:
            _name(outcome)


class WorkflowRegistry:
    def __init__(self):
        self._nodes: dict[str, NodeDefinition] = {}
        self._routers: dict[str, RouterDefinition] = {}

    def register_node(self, name: str, definition: NodeDefinition) -> None:
        self._register(self._nodes, name, definition)

    def register_router(self, name: str, definition: RouterDefinition) -> None:
        self._register(self._routers, name, definition)

    @staticmethod
    def _register(registry: dict, name: str, definition) -> None:
        _name(name)
        if name in registry:
            raise ValueError(f"Already registered: {name!r}")
        registry[name] = definition

    def node(self, name: str) -> NodeDefinition:
        try:
            return self._nodes[name]
        except KeyError as exc:
            raise WorkflowConfigurationError(f"Unknown node kind {name!r}") from exc

    def router(self, name: str) -> RouterDefinition:
        try:
            return self._routers[name]
        except KeyError as exc:
            raise WorkflowConfigurationError(f"Unknown router {name!r}") from exc
