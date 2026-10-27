"""Compile validated JSON and trusted node contracts into a runnable LangGraph."""

import asyncio
from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
import inspect
from types import MappingProxyType
from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, TypeAdapter, ValidationError
from typing_extensions import TypedDict

from backend.config.workflows import WorkflowConfigurationError, WorkflowSpec, parse_workflow_spec
from backend.workflows.build_trace import GraphBuildTrace, qualified_name, type_name
from backend.workflows.registry import StateField, WorkflowRegistry
from backend.workflows.validation import validate_workflow


class WorkflowExecutionError(ValueError):
    """A runtime input, node update, or route violates its declared contract."""


def _revalidate_models(value):
    # Pydantic otherwise trusts an existing model, including an instance mutated
    # after construction or built using model_construct(). Preserve concrete types.
    if isinstance(value, BaseModel):
        return type(value).model_validate(
            {name: _revalidate_models(item) for name, item in value},
            strict=True, extra="forbid",
        )
    if isinstance(value, list):
        return [_revalidate_models(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_revalidate_models(item) for item in value)
    if isinstance(value, dict):
        return {name: _revalidate_models(item) for name, item in value.items()}
    return value


def _validate_values(values, adapters, label):
    validated = {}
    for name, value in values.items():
        try:
            validated[name] = adapters[name].validate_python(
                _revalidate_models(value), strict=True, extra="forbid",
            )
        except ValidationError as exc:
            raise WorkflowExecutionError(f"{label}: invalid field {name!r}: {exc}") from exc
    return validated


def _input_view(state, inputs, adapters, label):
    missing = inputs.keys() - state.keys()
    if missing:
        raise WorkflowExecutionError(f"{label}: missing inputs {sorted(missing)}")
    values = {name: state[name] for name in inputs}
    # Preserve model instances (e.g. MessageAnalysis); do not recursively model_dump.
    # A private copy stops accidental nested mutation from bypassing updates.
    return MappingProxyType(deepcopy(_validate_values(values, adapters, label)))


def _wrap_node(node_id, definition, node, adapters):
    is_async = inspect.iscoroutinefunction(node) or inspect.iscoroutinefunction(getattr(node, "__call__", None))

    async def execute(state):
        label = f"Node {node_id!r}"
        inputs = _input_view(state, definition.inputs, adapters, label)
        update = await node(inputs) if is_async else await asyncio.to_thread(node, inputs)
        if inspect.isawaitable(update):
            update = await update
        if not isinstance(update, dict):
            raise WorkflowExecutionError(f"{label}: must return a dictionary of state updates")
        unknown = update.keys() - definition.outputs.keys()
        if unknown:
            raise WorkflowExecutionError(f"{label}: undeclared output fields {sorted(unknown)}")
        missing = definition.outputs.keys() - definition.optional_outputs - update.keys()
        if missing:
            raise WorkflowExecutionError(f"{label}: missing required outputs {sorted(missing)}")
        return _validate_values(update, adapters, label)

    return execute


def _wrap_router(source, definition, adapters):
    def route(state):
        label = f"Router after {source!r}"
        inputs = _input_view(state, definition.inputs, adapters, label)
        outcome = definition.route(inputs)
        if not isinstance(outcome, str) or outcome not in definition.outcomes:
            raise WorkflowExecutionError(f"{label}: undeclared outcome {outcome!r}")
        return outcome

    return route


class CompiledWorkflowGraph:
    """Validated invocation boundary around the native graph.

    Every invocation supplies all initial fields. Non-input fields are cleared at
    the start of each turn, so checkpoints cannot satisfy a missing current output.
    Reducers are restricted to explicitly initialized fields (e.g. messages).
    """

    def __init__(self, graph, state_schema, fields, initial_fields, adapters, step_limit):
        self.graph = graph
        self.state_schema = state_schema
        self.fields = MappingProxyType(dict(fields))
        self.initial_fields = MappingProxyType(dict(initial_fields))
        self._adapters = adapters
        self._step_limit = step_limit

    async def ainvoke(self, initial: dict[str, Any], config=None):
        if not isinstance(initial, dict):
            raise WorkflowExecutionError("Initial state must be a dictionary")
        missing = self.initial_fields.keys() - initial.keys()
        unknown = initial.keys() - self.initial_fields.keys()
        if missing or unknown:
            raise WorkflowExecutionError(
                f"Initial state: missing fields {sorted(missing)}; undeclared fields {sorted(unknown)}"
            )
        values = _validate_values(initial, self._adapters, "Initial state")
        reset = {name: None for name in self.fields.keys() - self.initial_fields.keys()}
        # Derived fields start unavailable. Contracts control when they can be read.
        state = {**reset, **deepcopy(values)}
        config = {**(config or {}), "recursion_limit": self._step_limit}
        return await self.graph.ainvoke(state, config=config)

    async def aget_state(self, config):
        return await self.graph.aget_state(config)


def compile_workflow(
    definition: str | dict | WorkflowSpec,
    registry: WorkflowRegistry,
    *,
    initial_fields: Mapping[str, StateField],
    protected_fields: frozenset[str] = frozenset(),
    required_outputs: frozenset[str] = frozenset(),
    checkpointer=None,
    build_trace: GraphBuildTrace | None = None,
) -> CompiledWorkflowGraph:
    """Validate before invoking any node factory or building the graph.

    Factories receive services through trusted Python closures. JSON controls node
    selection and topology; it cannot import code or acquire credentials.
    """
    context = nullcontext(build_trace) if build_trace is not None else GraphBuildTrace()
    with context as trace:
        return _compile_workflow(
            definition, registry, initial_fields=initial_fields,
            protected_fields=protected_fields, required_outputs=required_outputs,
            checkpointer=checkpointer, trace=trace,
        )


def _compile_workflow(
    definition, registry, *, initial_fields, protected_fields, required_outputs, checkpointer, trace,
):
    trace.stage("parse")
    spec = parse_workflow_spec(definition)
    trace.workflow = spec.name
    trace.record(
        "definition_loaded", schema_version=spec.schema_version,
        nodes=len(spec.nodes), edges=len(spec.edges), routes=len(spec.routes),
    )
    for node in spec.nodes:
        trace.record("node_selected", node_id=node.id, kind=node.kind, needs=node.needs)
    initial_fields = dict(initial_fields)
    trace.stage("validate")
    try:
        plan = validate_workflow(
            spec, registry, initial_fields=initial_fields,
            protected_fields=protected_fields, required_outputs=required_outputs,
        )
    except WorkflowConfigurationError as exc:
        trace.record("validation_failed", reason=str(exc))
        raise
    trace.record("validation_succeeded")
    trace.stage("state_schema")
    adapters = {name: TypeAdapter(field.annotation) for name, field in plan.fields.items()}
    annotations = {}
    for name, field in plan.fields.items():
        # Before its producer runs, a derived channel contains the reset marker None.
        annotation = field.annotation if name in initial_fields else field.annotation | None
        annotations[name] = Annotated[annotation, field.reducer] if field.reducer else annotation
        trace.record(
            "state_field", name=name, type=type_name(field.annotation),
            storage_type=type_name(annotation),
            reducer=qualified_name(field.reducer) if field.reducer else None,
            initial=name in initial_fields, protected=name in protected_fields,
            required_output=name in required_outputs,
        )
    state_schema = TypedDict(f"{spec.name}_state", annotations, total=False)
    builder = StateGraph(state_schema)
    kinds = {node.id: node.kind for node in spec.nodes}
    specifications = {node.id: node for node in spec.nodes}
    for node_id, node_definition in plan.nodes.items():
        trace.stage("node_factory", node_id=node_id, kind=kinds[node_id])
        node = (
            node_definition.binding_factory(specifications[node_id])
            if node_definition.binding_factory is not None else node_definition.factory()
        )
        if not callable(node):
            raise TypeError(f"Factory for node {node_id!r} must return a callable")
        builder.add_node(node_id, _wrap_node(node_id, node_definition, node, adapters))
        trace.record(
            "node_added", node_id=node_id, kind=kinds[node_id], implementation=qualified_name(node),
            needs=list(node_definition.inputs), outputs=list(node_definition.outputs),
            optional_outputs=sorted(node_definition.optional_outputs),
        )

    trace.stage("connections")
    endpoints = {"START": START, "END": END}
    for edge in spec.edges:
        builder.add_edge(endpoints.get(edge.source, edge.source), endpoints.get(edge.target, edge.target))
        trace.record("edge_added", from_node=edge.source, to_node=edge.target)
    for route in spec.routes:
        builder.add_conditional_edges(
            route.source,
            _wrap_router(route.source, plan.routers[route.source], adapters),
            {outcome: endpoints.get(target, target) for outcome, target in route.paths.items()},
        )
        trace.record(
            "route_added", from_node=route.source, router=route.router,
            needs=list(plan.routers[route.source].inputs), paths=route.paths,
        )
    trace.stage("compile", checkpointer=qualified_name(checkpointer) if checkpointer is not None else None)
    graph = builder.compile(checkpointer=checkpointer, name=spec.name)
    trace.record("graph_compiled", state_schema=state_schema.__name__)
    compiled = CompiledWorkflowGraph(
        graph, state_schema, plan.fields, initial_fields, adapters, len(spec.nodes) + 2,
    )
    compiled.build_trace_path = trace.path
    compiled.build_id = trace.build_id
    return compiled
