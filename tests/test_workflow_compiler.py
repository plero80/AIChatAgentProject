"""Compiler contracts, graph path checks, and runtime/checkpoint boundaries."""

import asyncio
from copy import deepcopy
import json
from typing import get_type_hints
from unittest.mock import Mock

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict
import pytest

from backend.config.workflows import (
    WorkflowConfigurationError, WorkflowSpec, load_workflow_spec, parse_workflow_spec,
)
from backend.workflows.compiler import WorkflowExecutionError, compile_workflow
from backend.workflows.registry import NodeDefinition, RouterDefinition, StateField, WorkflowRegistry


TEXT = StateField(str)
COUNT = StateField(int)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word_count: int


ANALYSIS = StateField(Analysis)


def manifest(nodes=None, edges=None, routes=None):
    return {
        "schema_version": 1,
        "name": "example",
        "nodes": nodes if nodes is not None else [
            {"id": "analyze", "kind": "analyzer", "needs": ["message"]},
            {"id": "respond", "kind": "presenter", "needs": ["analysis"]},
        ],
        "edges": [{"from": source, "to": target} for source, target in (
            edges if edges is not None else [("START", "analyze"), ("analyze", "respond"), ("respond", "END")]
        )],
        "routes": routes or [],
    }


def make_registry(*, analysis_update=None, reply_node=None):
    async def analyze(state):
        assert set(state) == {"message"}
        return analysis_update if analysis_update is not None else {
            "analysis": {"word_count": len(state["message"].split())},
        }

    def respond(state):
        assert set(state) == {"analysis"}
        assert isinstance(state["analysis"], Analysis)
        return {"reply": f"Words: {state['analysis'].word_count}"}

    registry = WorkflowRegistry()
    analyzer_factory = Mock(return_value=analyze)
    registry.register_node("analyzer", NodeDefinition(analyzer_factory, {"message": TEXT}, {"analysis": ANALYSIS}))
    registry.register_node("presenter", NodeDefinition(
        lambda: reply_node or respond, {"analysis": ANALYSIS}, {"reply": TEXT},
    ))
    return registry, analyzer_factory


def compile_example(definition=None, registry=None, **kwargs):
    registry = registry if registry is not None else make_registry()[0]
    return compile_workflow(
        definition if definition is not None else manifest(), registry,
        initial_fields={"message": TEXT}, required_outputs=frozenset({"reply"}), **kwargs,
    )


def test_json_compiles_dynamic_state_and_preserves_structured_models():
    registry, factory = make_registry()
    graph = compile_example(json.dumps(manifest()), registry)
    assert set(graph.fields) == {"message", "analysis", "reply"}
    assert get_type_hints(graph.state_schema)["analysis"] == Analysis | None
    result = asyncio.run(graph.ainvoke({"message": "hello world"}))
    assert result["analysis"] == Analysis(word_count=2)
    assert result["reply"] == "Words: 2"
    factory.assert_called_once_with()


@pytest.mark.parametrize("initial,error", [
    ({}, "missing fields"),
    ({"message": "Hi", "client_id": "injected"}, "undeclared fields"),
    ({"message": 1}, "invalid field 'message'"),
    ([], "must be a dictionary"),
])
def test_runtime_rejects_invalid_initial_state(initial, error):
    with pytest.raises(WorkflowExecutionError, match=error):
        asyncio.run(compile_example().ainvoke(initial))


@pytest.mark.parametrize("update,error", [
    ({}, "missing required outputs"),
    ({"analysis": {"word_count": "two"}}, "invalid field 'analysis'"),
    ({"analysis": {"word_count": "2"}}, "invalid field 'analysis'"),
    ({"analysis": {"word_count": 2, "unexpected": True}}, "invalid field 'analysis'"),
    ({"analysis": None}, "invalid field 'analysis'"),
    ({"analysis": Analysis.model_construct(word_count="broken")}, "invalid field 'analysis'"),
    ({"analysis": {"word_count": 2}, "client_id": "other"}, "undeclared output"),
    ([], "must return a dictionary"),
])
def test_invalid_llm_or_node_updates_fail_before_the_consumer(update, error):
    consumer = Mock(side_effect=AssertionError("Consumer must not execute"))
    registry, _ = make_registry(analysis_update=update, reply_node=consumer)
    with pytest.raises(WorkflowExecutionError, match=error):
        asyncio.run(compile_example(registry=registry).ainvoke({"message": "hello"}))
    consumer.assert_not_called()


@pytest.mark.parametrize("change,error", [
    (lambda spec: spec.update(schema_version=2), "schema_version"),
    (lambda spec: spec.update(schema_version=True), "must be an integer"),
    (lambda spec: spec.update(import_path="os.system"), "Extra inputs"),
    (lambda spec: spec["nodes"][0].update(kind="not_registered"), "Unknown node kind"),
    (lambda spec: spec["nodes"][0].update(kind="os.system"), "kind"),
    (lambda spec: spec["nodes"][0].update(needs=[]), "needs must match"),
    (lambda spec: spec["nodes"][0].update(needs=["message", "message"]), "repeats a needs"),
    (lambda spec: spec["nodes"].append(deepcopy(spec["nodes"][0])), "Duplicate node"),
    (lambda spec: spec["edges"][0].update(to="unknown"), "Invalid edge destination"),
    (lambda spec: spec["edges"][0].update(to="START"), "Invalid edge destination"),
    (lambda spec: spec["edges"][0].update({"from": "END"}), "Invalid edge source"),
    (lambda spec: spec["edges"].append({"from": "analyze", "to": "END"}), "multiple transitions"),
    (lambda spec: spec["edges"].pop(), "outgoing edge"),
    (lambda spec: spec["edges"][0].update(to="respond"), "unreachable"),
    (lambda spec: spec["edges"][2].update(to="analyze"), "cycles are unsupported"),
])
def test_bad_definitions_fail_before_any_factory_runs(change, error):
    registry, factory = make_registry()
    spec = manifest()
    change(spec)
    with pytest.raises(WorkflowConfigurationError, match=error):
        compile_example(spec, registry)
    factory.assert_not_called()


def test_reading_a_field_before_its_producer_is_rejected():
    spec = manifest(edges=[("START", "respond"), ("respond", "analyze"), ("analyze", "END")])
    with pytest.raises(WorkflowConfigurationError, match="not available on every incoming path"):
        compile_example(spec)


def test_missing_producer_and_incompatible_consumer_types_are_rejected():
    registry, _ = make_registry()
    spec = manifest(nodes=[manifest()["nodes"][1]], edges=[("START", "respond"), ("respond", "END")])
    with pytest.raises(WorkflowConfigurationError, match="no input or node produces"):
        compile_example(spec, registry)
    registry.register_node("bad_presenter", NodeDefinition(lambda: lambda state: {}, {"analysis": TEXT}, {"reply": TEXT}))
    spec = manifest()
    spec["nodes"][1]["kind"] = "bad_presenter"
    with pytest.raises(WorkflowConfigurationError, match="incompatible type"):
        compile_example(spec, registry)


def branching_definition(*, right_produces=True, outcome=None):
    registry = WorkflowRegistry()
    registry.register_node("gate", NodeDefinition(lambda: lambda state: {}, {"message": TEXT}, {}))
    for kind in ("left", "right"):
        writes = kind == "left" or right_produces
        registry.register_node(kind, NodeDefinition(
            lambda writes=writes: lambda state: {"count": 7} if writes else {},
            {}, {"count": COUNT} if writes else {},
        ))
    registry.register_node("finish", NodeDefinition(
        lambda: lambda state: {"reply": str(state["count"])}, {"count": COUNT}, {"reply": TEXT},
    ))
    registry.register_router("choose", RouterDefinition(
        lambda state: outcome if outcome is not None else state["message"],
        {"message": TEXT}, frozenset({"left", "right"}),
    ))
    spec = manifest(
        nodes=[
            {"id": "gate_step", "kind": "gate", "needs": ["message"]},
            {"id": "left_step", "kind": "left", "needs": []},
            {"id": "right_step", "kind": "right", "needs": []},
            {"id": "finish_step", "kind": "finish", "needs": ["count"]},
        ],
        edges=[("START", "gate_step"), ("left_step", "finish_step"), ("right_step", "finish_step"), ("finish_step", "END")],
        routes=[{"from": "gate_step", "router": "choose", "paths": {"left": "left_step", "right": "right_step"}}],
    )
    return spec, registry


def test_conditional_merge_requires_values_on_every_path():
    spec, registry = branching_definition(right_produces=False)
    with pytest.raises(WorkflowConfigurationError, match="Node 'finish_step'.*not available"):
        compile_example(spec, registry)


@pytest.mark.parametrize("branch", ["left", "right"])
def test_both_conditional_paths_run_with_compatible_producers(branch):
    spec, registry = branching_definition()
    graph = compile_example(spec, registry)
    assert asyncio.run(graph.ainvoke({"message": branch}))["reply"] == "7"


@pytest.mark.parametrize("outcome", ["unknown", ["left", "right"], None])
def test_routers_cannot_invent_routes_or_return_parallel_destinations(outcome):
    spec, registry = branching_definition(outcome=outcome)
    with pytest.raises(WorkflowExecutionError, match="undeclared outcome"):
        asyncio.run(compile_example(spec, registry).ainvoke({"message": "unknown"}))


@pytest.mark.parametrize("change,error", [
    (lambda spec: spec["routes"][0].update(router="unknown"), "Unknown router"),
    (lambda spec: spec["routes"][0]["paths"].pop("right"), "paths must match"),
    (lambda spec: spec["edges"].append({"from": "gate_step", "to": "END"}), "multiple transitions"),
    (lambda spec: spec["routes"][0]["paths"].update(right="gate_step"), "unreachable"),
    (lambda spec: spec["edges"][1].update(to="gate_step"), "Cycles are unsupported"),
])
def test_invalid_conditional_graphs_are_rejected(change, error):
    spec, registry = branching_definition()
    change(spec)
    with pytest.raises(WorkflowConfigurationError, match=error):
        compile_example(spec, registry)


def test_conflicting_producers_and_protected_identity_writes_are_rejected():
    registry = WorkflowRegistry()
    registry.register_node("first", NodeDefinition(lambda: lambda state: {}, {}, {"client_id": TEXT}))
    registry.register_node("second", NodeDefinition(lambda: lambda state: {}, {}, {"client_id": COUNT}))
    spec = manifest(
        nodes=[{"id": "a", "kind": "first", "needs": []}, {"id": "b", "kind": "second", "needs": []}],
        edges=[("START", "a"), ("a", "b"), ("b", "END")],
    )
    with pytest.raises(WorkflowConfigurationError, match="conflicting type"):
        compile_workflow(spec, registry, initial_fields={})
    with pytest.raises(WorkflowConfigurationError, match="cannot write protected"):
        compile_workflow(spec, registry, initial_fields={"client_id": TEXT}, protected_fields=frozenset({"client_id"}))


def test_undeclared_read_is_unavailable_and_nested_mutation_does_not_change_state():
    def naughty(state):
        with pytest.raises(KeyError):
            _ = state["message"]
        with pytest.raises(TypeError):
            state["analysis"] = Analysis(word_count=99)
        state["analysis"].word_count = 99
        return {"reply": "changed local copy"}

    registry, _ = make_registry(reply_node=naughty)
    state = asyncio.run(compile_example(registry=registry).ainvoke({"message": "hello"}))
    assert state["analysis"].word_count == 1


def test_optional_output_does_not_satisfy_a_required_consumer():
    registry, _ = make_registry()
    registry.register_node("maybe_analyzer", NodeDefinition(
        lambda: lambda state: {}, {"message": TEXT}, {"analysis": ANALYSIS},
        optional_outputs=frozenset({"analysis"}),
    ))
    spec = manifest()
    spec["nodes"][0]["kind"] = "maybe_analyzer"
    with pytest.raises(WorkflowConfigurationError, match="not available on every incoming path"):
        compile_example(spec, registry)


def test_checkpointed_optional_values_are_cleared_between_turns_and_sessions_are_isolated():
    registry = WorkflowRegistry()
    registry.register_node("maybe", NodeDefinition(
        lambda: lambda state: {"reply": state["message"]} if state["message"] != "skip" else {},
        {"message": TEXT}, {"reply": TEXT}, optional_outputs=frozenset({"reply"}),
    ))
    spec = manifest(nodes=[{"id": "step", "kind": "maybe", "needs": ["message"]}], edges=[("START", "step"), ("step", "END")])
    graph = compile_workflow(spec, registry, initial_fields={"message": TEXT}, checkpointer=InMemorySaver())

    async def run():
        a = {"configurable": {"thread_id": "client-a:session"}}
        b = {"configurable": {"thread_id": "client-b:session"}}
        assert (await graph.ainvoke({"message": "first"}, a))["reply"] == "first"
        assert (await graph.ainvoke({"message": "other"}, b))["reply"] == "other"
        assert (await graph.ainvoke({"message": "skip"}, a))["reply"] is None
        assert (await graph.aget_state(b)).values["reply"] == "other"

    asyncio.run(run())


def test_service_failures_propagate():
    async def fail(state):
        raise RuntimeError("service failed")

    registry, _ = make_registry(reply_node=fail)
    with pytest.raises(RuntimeError, match="service failed"):
        asyncio.run(compile_example(registry=registry).ainvoke({"message": "hello"}))


def test_cancelling_an_invocation_cancels_the_running_node():
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def wait(state):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        registry, _ = make_registry(reply_node=wait)
        task = asyncio.create_task(compile_example(registry=registry).ainvoke({"message": "hello"}))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_load_parse_and_schema_export(tmp_path):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8-sig")
    assert load_workflow_spec(path).name == "example"
    schema = WorkflowSpec.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["NodeSpec"]["properties"]["needs"]["type"] == "array"
    with pytest.raises(WorkflowConfigurationError, match="Cannot read"):
        load_workflow_spec(tmp_path / "missing.json")
    with pytest.raises(WorkflowConfigurationError, match="Invalid workflow definition"):
        parse_workflow_spec("not json")
    spec = parse_workflow_spec(manifest())
    spec.nodes[0].needs.append("unexpected")
    with pytest.raises(WorkflowConfigurationError, match="needs must match"):
        compile_example(spec)


def test_every_terminal_path_must_produce_required_outputs():
    spec, registry = branching_definition()
    spec["edges"][1]["to"] = "END"
    with pytest.raises(WorkflowConfigurationError, match="END lacks guaranteed output fields"):
        compile_example(spec, registry)


def test_router_inputs_need_a_guaranteed_producer():
    spec, registry = branching_definition()
    registry.register_router("needs_count", RouterDefinition(
        lambda state: "left", {"count": COUNT}, frozenset({"left", "right"}),
    ))
    spec["routes"][0]["router"] = "needs_count"
    with pytest.raises(WorkflowConfigurationError, match="Router after 'gate_step'.*not available"):
        compile_example(spec, registry)


def test_reducer_requires_initialization_and_consistent_semantics():
    from operator import add, mul

    registry = WorkflowRegistry()
    registry.register_node("accumulate", NodeDefinition(
        lambda: lambda state: {"count": 1}, {}, {"count": StateField(int, reducer=add)},
    ))
    spec = manifest(nodes=[{"id": "step", "kind": "accumulate", "needs": []}], edges=[("START", "step"), ("step", "END")])
    with pytest.raises(WorkflowConfigurationError, match="must have an initial input"):
        compile_workflow(spec, registry, initial_fields={})
    with pytest.raises(WorkflowConfigurationError, match="conflicting type or reducer"):
        compile_workflow(spec, registry, initial_fields={"count": StateField(int, reducer=mul)})
    graph = compile_workflow(spec, registry, initial_fields={"count": StateField(int, reducer=add)}, checkpointer=InMemorySaver())

    async def run():
        config = {"configurable": {"thread_id": "counter"}}
        assert (await graph.ainvoke({"count": 0}, config))["count"] == 1
        assert (await graph.ainvoke({"count": 0}, config))["count"] == 2

    asyncio.run(run())


def test_sync_callable_can_return_an_awaitable():
    async def respond(state):
        return {"reply": "async result"}

    registry, _ = make_registry(reply_node=lambda state: respond(state))
    assert asyncio.run(compile_example(registry=registry).ainvoke({"message": "Hi"}))["reply"] == "async result"


@pytest.mark.parametrize("change", [
    lambda spec: spec["routes"][0]["paths"].update(chat="respond"),
    lambda spec: spec["routes"].clear(),
    lambda spec: spec["nodes"].pop(1),
])
def test_recipe_policy_requires_intent_routes_to_matching_business_nodes(change):
    from backend.graphs.compiled_recipe_chat_graph import validate_recipe_policy
    from backend.paths import CONFIG_DIR

    spec = load_workflow_spec(CONFIG_DIR / "workflows" / "recipe_chat.json").model_dump(by_alias=True)
    change(spec)
    with pytest.raises(WorkflowConfigurationError, match="Recipe"):
        validate_recipe_policy(parse_workflow_spec(spec))
