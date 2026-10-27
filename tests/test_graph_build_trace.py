"""Construction traces must describe builds without changing execution behavior."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from backend.config.workflows import WorkflowConfigurationError
from backend.workflows import build_trace
from backend.workflows.compiler import compile_workflow
from backend.workflows.example import build_example
from backend.workflows.registry import NodeDefinition, StateField, WorkflowRegistry


def events(path=None):
    path = path or Path(os.environ["GRAPH_BUILD_LOG"])
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_build_trace_describes_fields_implementations_and_topology_without_logging_requests():
    graph = build_example()
    records = events(graph.build_trace_path)
    assert records[0]["event"] == "build_started"
    assert records[-1]["event"] == "build_succeeded"
    assert {record["build_id"] for record in records} == {graph.build_id}
    assert records[-1]["workflow"] == "word_count_example"
    fields = {record["name"]: record for record in records if record["event"] == "state_field"}
    assert fields["message"]["initial"] and fields["message"]["protected"]
    assert fields["analysis"]["type"].endswith("TextAnalysis")
    assert not fields["analysis"]["initial"]
    assert fields["reply"]["required_output"]
    nodes = [record for record in records if record["event"] == "node_added"]
    assert [(node["node_id"], node["kind"]) for node in nodes] == [("analyze", "word_analysis"), ("respond", "word_reply")]
    assert nodes[0]["implementation"].endswith("build_example.<locals>.analyze")
    assert nodes[1]["needs"] == ["analysis"]
    assert nodes[1]["outputs"] == ["reply"]
    assert [(r["from_node"], r["to_node"]) for r in records if r["event"] == "edge_added"] == [
        ("START", "analyze"), ("analyze", "respond"), ("respond", "END"),
    ]
    before = graph.build_trace_path.read_text(encoding="utf-8")
    asyncio.run(graph.ainvoke({"message": "private conversation text"}))
    assert graph.build_trace_path.read_text(encoding="utf-8") == before


def test_parser_failure_has_a_location_but_does_not_copy_rejected_values():
    with pytest.raises(WorkflowConfigurationError):
        compile_workflow({"private_token": "do-not-log-this-token"}, WorkflowRegistry(), initial_fields={})
    records = events()
    failure = records[-1]
    assert failure["event"] == "build_failed"
    assert failure["phase"] == "parse"
    assert failure["error_type"] == "WorkflowConfigurationError"
    assert {tuple(issue["location"]) for issue in failure["issues"]} >= {("nodes",), ("private_token",)}
    assert "do-not-log-this-token" not in json.dumps(records)


def test_dependency_failure_logs_reason_before_constructing_nodes():
    spec = {
        "schema_version": 1, "name": "invalid",
        "nodes": [{"id": "step", "kind": "unknown", "needs": []}],
        "edges": [{"from": "START", "to": "step"}, {"from": "step", "to": "END"}],
    }
    with pytest.raises(WorkflowConfigurationError, match="Unknown node kind"):
        compile_workflow(spec, WorkflowRegistry(), initial_fields={})
    records = events()
    assert next(r for r in records if r["event"] == "validation_failed")["reason"] == "Unknown node kind 'unknown'"
    assert records[-1]["phase"] == "validate"
    assert not any(r["event"] == "node_added" for r in records)


def test_factory_failure_preserves_original_exception_and_identifies_the_node():
    error = RuntimeError("private service configuration")

    def broken_factory():
        raise error

    registry = WorkflowRegistry()
    registry.register_node("broken", NodeDefinition(broken_factory, {}, {"reply": StateField(str)}))
    spec = {
        "schema_version": 1, "name": "broken_example",
        "nodes": [{"id": "respond", "kind": "broken", "needs": []}],
        "edges": [{"from": "START", "to": "respond"}, {"from": "respond", "to": "END"}],
    }
    with pytest.raises(RuntimeError) as caught:
        compile_workflow(spec, registry, initial_fields={})
    assert caught.value is error
    records = events()
    assert records[-1]["phase"] == "node_factory"
    assert records[-1]["node_id"] == "respond"
    assert records[-1]["kind"] == "broken"
    assert "private service configuration" not in json.dumps(records)


def test_repeated_and_concurrent_builds_append_complete_distinct_traces():
    with ThreadPoolExecutor(max_workers=3) as executor:
        graphs = list(executor.map(lambda _: build_example(), range(3)))
    # A later build also appends, without duplicate handlers or truncation.
    graphs.append(build_example())
    records = events()
    ids = {graph.build_id for graph in graphs}
    assert len(ids) == 4
    assert {record["build_id"] for record in records} == ids
    for build_id in ids:
        build = [r for r in records if r["build_id"] == build_id]
        assert sum(r["event"] == "build_started" for r in build) == 1
        assert sum(r["event"] == "build_succeeded" for r in build) == 1
        assert sum(r["event"] == "node_added" for r in build) == 2


def test_unwritable_trace_destination_does_not_prevent_the_graph_from_running(tmp_path, monkeypatch, caplog):
    blocked = tmp_path / "file-not-directory"
    blocked.write_text("existing file", encoding="utf-8")
    monkeypatch.setenv("GRAPH_BUILD_LOG", str(blocked / "trace.jsonl"))
    graph = build_example()
    assert asyncio.run(graph.ainvoke({"message": "hello"}))["reply"] == "Your message contains 1 words."
    assert "Cannot open graph build trace" in caplog.text


@pytest.mark.parametrize("configured", [None, "custom/build.jsonl"])
def test_default_and_relative_paths_use_project_root_not_working_directory(tmp_path, monkeypatch, configured):
    monkeypatch.setattr(build_trace, "PROJECT_ROOT", tmp_path)
    if configured is None:
        monkeypatch.delenv("GRAPH_BUILD_LOG")
    else:
        monkeypatch.setenv("GRAPH_BUILD_LOG", configured)
    cwd = tmp_path / "unrelated"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    graph = build_example()
    expected = tmp_path / (configured or "output/graph_build.jsonl")
    assert graph.build_trace_path == expected
    assert events(expected)[-1]["event"] == "build_succeeded"
