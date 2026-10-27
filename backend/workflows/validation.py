"""Validate the supported acyclic, single-active-branch workflow language."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from backend.config.workflows import WorkflowConfigurationError, WorkflowSpec
from backend.workflows.registry import NodeDefinition, RouterDefinition, StateField, WorkflowRegistry


@dataclass(frozen=True)
class ValidatedWorkflow:
    spec: WorkflowSpec
    nodes: Mapping[str, NodeDefinition]
    routers: Mapping[str, RouterDefinition]
    fields: Mapping[str, StateField]


def validate_workflow(
    spec: WorkflowSpec,
    registry: WorkflowRegistry,
    *,
    initial_fields: Mapping[str, StateField],
    protected_fields: frozenset[str] = frozenset(),
    required_outputs: frozenset[str] = frozenset(),
) -> ValidatedWorkflow:
    def fail(message):
        raise WorkflowConfigurationError(message)

    nodes = {}
    for node in spec.nodes:
        if node.id in nodes:
            fail(f"Duplicate node id {node.id!r}")
        definition = registry.node(node.kind)
        if node.bindings and definition.binding_factory is None:
            fail(f"Node {node.id!r} does not support service bindings")
        if len(node.needs) != len(set(node.needs)):
            fail(f"Node {node.id!r} repeats a needs field")
        if set(node.needs) != definition.inputs.keys():
            fail(f"Node {node.id!r} needs must match its Python contract: {sorted(definition.inputs)}")
        nodes[node.id] = definition

    if not protected_fields <= initial_fields.keys():
        fail("Protected fields must be declared initial inputs")
    fields = dict(initial_fields)
    for node_id, definition in nodes.items():
        for name, field in definition.outputs.items():
            if name in protected_fields:
                fail(f"Node {node_id!r} cannot write protected field {name!r}")
            if name in fields and fields[name] != field:
                fail(f"Node {node_id!r} has a conflicting type or reducer for field {name!r}")
            if field.reducer is not None and name not in initial_fields:
                fail(f"Reducer field {name!r} must have an initial input in schema version 1")
            fields[name] = field
    if set(nodes) & fields.keys():
        fail(f"Node ids cannot also be state field names: {sorted(set(nodes) & fields.keys())}")

    successors = {name: set() for name in ["START", *nodes, "END"]}
    predecessors = {name: set() for name in successors}
    transitions = {}
    routers = {}

    def connect(source, target):
        if source not in successors or source == "END":
            fail(f"Invalid edge source {source!r}")
        if target not in successors or target == "START":
            fail(f"Invalid edge destination {target!r}")
        successors[source].add(target)
        predecessors[target].add(source)

    for edge in spec.edges:
        if edge.source in transitions:
            fail(f"Node {edge.source!r} has multiple transitions; fan-out is unsupported")
        transitions[edge.source] = "edge"
        connect(edge.source, edge.target)
    for route in spec.routes:
        if route.source not in nodes:
            fail(f"Invalid router source {route.source!r}")
        if route.source in transitions:
            fail(f"Node {route.source!r} has multiple transitions; choose one edge or router")
        definition = registry.router(route.router)
        if route.paths.keys() != definition.outcomes:
            fail(f"Router {route.router!r} paths must match outcomes {sorted(definition.outcomes)}")
        transitions[route.source] = "route"
        routers[route.source] = definition
        for target in route.paths.values():
            connect(route.source, target)
    for source in ["START", *nodes]:
        if source not in transitions:
            fail(f"Node {source!r} needs an outgoing edge or router (use END to finish)")

    reachable = set()
    pending = ["START"]
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(successors[name])
    missing = set(nodes) - reachable
    if missing:
        fail(f"Nodes are unreachable from START: {sorted(missing)}")
    if "END" not in reachable:
        fail("No execution path reaches END (cycles are unsupported)")

    indegree = {name: len(parents) for name, parents in predecessors.items()}
    queue = deque(name for name, degree in indegree.items() if degree == 0)
    order = []
    while queue:
        name = queue.popleft()
        order.append(name)
        for child in sorted(successors[name]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(successors):
        fail("Cycles are unsupported in workflow schema version 1")

    def check_inputs(label, inputs, available):
        for name, expected in inputs.items():
            if name not in fields:
                fail(f"{label} needs field {name!r}, but no input or node produces it")
            if fields[name] != expected:
                fail(f"{label} expects an incompatible type or reducer for field {name!r}")
            if name not in available:
                fail(f"{label} needs field {name!r}, which is not available on every incoming path")

    available_after = {"START": set(initial_fields)}
    for name in order:
        if name == "START":
            continue
        # Conditional branches are alternatives, so a merge gets the intersection.
        available = set.intersection(*(available_after[parent] for parent in predecessors[name]))
        if name == "END":
            if not required_outputs <= available:
                fail(f"END lacks guaranteed output fields: {sorted(required_outputs - available)}")
            continue
        definition = nodes[name]
        check_inputs(f"Node {name!r}", definition.inputs, available)
        available = available | (definition.outputs.keys() - definition.optional_outputs)
        if name in routers:
            check_inputs(f"Router after {name!r}", routers[name].inputs, available)
        available_after[name] = available

    return ValidatedWorkflow(spec, nodes, routers, fields)
