import asyncio
import threading

import pytest

from backend.graphs.base_graph import BaseGraph
from backend.graphs.graph_requests import RecipeIngestionRequest
from backend.graphs.recipe_ingestion_graph import RecipeIngestionGraph


def test_graph_requires_async_execution_and_provides_sync_execution():
    with pytest.raises(TypeError):
        BaseGraph()

    class EchoGraph(BaseGraph[str, str]):
        async def arun(self, request: str) -> str:
            return request

    graph = EchoGraph()
    assert graph.run("sync") == "sync"
    assert asyncio.run(graph.arun("async")) == "async"


def test_ingestion_awaits_workflow_and_offloads_its_synchronous_nodes():
    calls = []
    event_loop_thread = threading.get_ident()

    def extract(state):
        calls.append((state["raw_recipe"], "extract", threading.get_ident()))
        return {"embedding_text": state["raw_recipe"]}

    def prepare(state):
        calls.append((state["raw_recipe"], "prepare", threading.get_ident()))
        return {"embedding_text": state["embedding_text"] + " prepared"}

    def embed(state):
        calls.append((state["raw_recipe"], "embed", threading.get_ident()))
        assert state["embedding_text"] == state["raw_recipe"] + " prepared"
        return {"embedding": [0.1]}

    def persist(state):
        calls.append((state["raw_recipe"], "persist", threading.get_ident()))
        assert state["embedding"] == [0.1]
        return {"success": True}

    graph = RecipeIngestionGraph(extract, prepare, embed, persist)

    async def requests():
        return await asyncio.gather(
            graph.arun(RecipeIngestionRequest("recipe A")),
            graph.arun(RecipeIngestionRequest("recipe B")),
        )

    a, b = asyncio.run(requests())
    assert a["success"] and b["success"]
    assert a["raw_recipe"] == "recipe A"
    assert b["raw_recipe"] == "recipe B"
    for recipe in ("recipe A", "recipe B"):
        assert [stage for name, stage, _ in calls if name == recipe] == ["extract", "prepare", "embed", "persist"]
    assert all(thread != event_loop_thread for _, _, thread in calls)
