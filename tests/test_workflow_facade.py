"""Runtime reuse, construction races, resource lifetime, and conversation isolation."""

import asyncio
from argparse import Namespace
from contextlib import asynccontextmanager
from typing import TypedDict

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
import pytest

from backend.cli.commands.chat_command import ChatCommand
from backend.clients.context import ClientContext
from backend.models.chat_response import ChatResponse
from backend.web import api
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.workflows.facade import WorkflowFacade
from backend.workflows.runtime import RuntimeCacheClosed


def client(name="chef_001", release="v1"):
    return ClientContext(client_id=name, display_name=name, release_id=release)


def turn(context=None, *, conversation="session", message="Hello"):
    context = context or client()
    return ConversationRequest(
        client_id=context.client_id, release_id=context.release_id,
        conversation_id=conversation, message=message,
    )


class State(TypedDict):
    message: str
    count: int


class CountingWorkflow(ChatWorkflow):
    def __init__(self, context, memory):
        super().__init__(context)
        graph = StateGraph(State)
        graph.add_node("increment", lambda state: {"count": state.get("count", 0) + 1})
        graph.add_edge(START, "increment")
        graph.add_edge("increment", END)
        self.graph = graph.compile(checkpointer=memory)

    async def _respond(self, request):
        result = await self.graph.ainvoke(
            {"message": request.message}, {"configurable": {"thread_id": request.thread_id}},
        )
        return ChatResponse(reply=f"{result['count']}: {request.message}")


def factory_with_history():
    # Shared persistence outlives the compiled runtime and its cache entry.
    memory = InMemorySaver()
    built, closed = [], []

    @asynccontextmanager
    async def factory(context):
        workflow = CountingWorkflow(context, memory)
        built.append(workflow)
        try:
            yield workflow
        finally:
            closed.append(workflow)

    return factory, built, closed


def test_one_runtime_serves_multiple_users_with_separate_checkpoint_state():
    async def run():
        factory, built, closed = factory_with_history()
        async with WorkflowFacade(factory) as facade:
            a = facade.for_client(client())
            b = facade.for_client(client())
            assert (await a.respond(turn(conversation="alice"))).reply == "1: Hello"
            assert (await b.respond(turn(conversation="bob"))).reply == "1: Hello"
            assert (await a.respond(turn(conversation="alice", message="Again"))).reply == "2: Again"
            assert len(built) == 1
            assert facade.cache.keys == (("chef_001", "v1"),)
        assert closed == built

    asyncio.run(run())


def test_different_clients_and_releases_get_different_runtimes():
    async def run():
        factory, built, closed = factory_with_history()
        async with WorkflowFacade(factory) as facade:
            for context in (client(), client("chef_002"), client(release="v2")):
                await facade.warm(context)
            assert len(built) == 3
            assert set(facade.cache.keys) == {("chef_001", "v1"), ("chef_002", "v1"), ("chef_001", "v2")}
        assert closed == built

    asyncio.run(run())


def test_simultaneous_first_requests_build_once_and_one_cancelled_waiter_does_not_cancel_build():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        built, closed = [], []

        @asynccontextmanager
        async def factory(context):
            built.append(context)
            started.set()
            await release.wait()
            try:
                yield CountingWorkflow(context, InMemorySaver())
            finally:
                closed.append(context)

        async with WorkflowFacade(factory) as facade:
            first = asyncio.create_task(facade.respond(client(), turn(conversation="a")))
            second = asyncio.create_task(facade.respond(client(), turn(conversation="b")))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                release.set()
                assert (await asyncio.wait_for(second, timeout=5)).reply == "1: Hello"
                assert len(built) == 1
            finally:
                release.set()
                await asyncio.gather(first, second, return_exceptions=True)
        assert closed == built

    asyncio.run(run())


def test_independent_client_builds_can_overlap():
    async def run():
        first_started, second_started = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def factory(context):
            if context.client_id == "a":
                first_started.set()
                await second_started.wait()
            else:
                second_started.set()
                await first_started.wait()
            yield CountingWorkflow(context, InMemorySaver())

        async with WorkflowFacade(factory) as facade:
            await asyncio.wait_for(asyncio.gather(facade.warm(client("a")), facade.warm(client("b"))), timeout=5)

    asyncio.run(run())


def test_failed_build_can_be_retried():
    async def run():
        attempts = 0

        @asynccontextmanager
        async def factory(context):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary construction error")
            yield CountingWorkflow(context, InMemorySaver())

        async with WorkflowFacade(factory) as facade:
            with pytest.raises(RuntimeError, match="temporary construction"):
                await facade.warm(client())
            assert facade.cache.keys == ()
            assert (await facade.respond(client(), turn())).reply == "1: Hello"
            assert attempts == 2

    asyncio.run(run())


@pytest.mark.parametrize("wrong_kind", ["type", "client", "release"])
def test_invalid_factory_output_is_closed_and_never_cached(wrong_kind):
    async def run():
        closed = []

        @asynccontextmanager
        async def factory(context):
            try:
                if wrong_kind == "type":
                    yield object()
                else:
                    wrong = client("other") if wrong_kind == "client" else client(release="other")
                    yield CountingWorkflow(wrong, InMemorySaver())
            finally:
                closed.append(True)

        async with WorkflowFacade(factory) as facade:
            with pytest.raises((ValueError, TypeError)):
                await facade.warm(client())
            assert closed == [True]
            assert facade.cache.keys == ()

    asyncio.run(run())


def test_lru_eviction_closes_only_the_least_recently_used_idle_runtime():
    async def run():
        factory, built, closed = factory_with_history()
        async with WorkflowFacade(factory, max_cached_workflows=2) as facade:
            await facade.warm(client("a"))
            await facade.warm(client("b"))
            await facade.warm(client("a"))
            await facade.warm(client("c"))
            assert facade.cache.keys == (("a", "v1"), ("c", "v1"))
            assert [workflow.client.client_id for workflow in closed] == ["b"]
        assert len(closed) == len(built) == 3

    asyncio.run(run())


def test_full_cache_waits_for_a_lease_instead_of_closing_an_active_runtime():
    async def run():
        factory, built, closed = factory_with_history()
        async with WorkflowFacade(factory, max_cached_workflows=1) as facade:
            async with facade.cache.acquire(client("a")) as active:
                waiting = asyncio.create_task(facade.warm(client("b")))
                await asyncio.sleep(0)
                assert len(built) == 1
                assert closed == []
                assert not waiting.done()
            await asyncio.wait_for(waiting, timeout=5)
            assert closed == [active]
            assert facade.cache.keys == (("b", "v1"),)

    asyncio.run(run())


def test_conversation_state_survives_runtime_eviction_and_rebuild():
    async def run():
        factory, built, _ = factory_with_history()
        async with WorkflowFacade(factory, max_cached_workflows=1) as facade:
            assert (await facade.respond(client("a"), turn(client("a")))).reply == "1: Hello"
            await facade.warm(client("b"))
            assert (await facade.respond(client("a"), turn(client("a")))).reply == "2: Hello"
            assert len(built) == 3

    asyncio.run(run())


def test_same_conversation_is_serialized_while_other_conversations_can_run():
    async def run():
        first_started, release = asyncio.Event(), asyncio.Event()
        calls = []

        class SlowWorkflow(ChatWorkflow):
            async def _respond(self, request):
                calls.append(request.message)
                if request.message == "first":
                    first_started.set()
                    await release.wait()
                return ChatResponse(reply=request.message)

        @asynccontextmanager
        async def factory(context):
            yield SlowWorkflow(context)

        async with WorkflowFacade(factory) as facade:
            first = asyncio.create_task(facade.respond(client(), turn(message="first")))
            await asyncio.wait_for(first_started.wait(), timeout=5)
            second = asyncio.create_task(facade.respond(client(), turn(message="second")))
            try:
                assert (await facade.respond(client(), turn(conversation="other", message="other"))).reply == "other"
                assert "second" not in calls
                release.set()
                await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
                assert calls == ["first", "other", "second"]
                assert facade._conversations == {}
            finally:
                release.set()
                await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(run())


def test_failed_turn_releases_conversation_lock_and_does_not_rebuild_runtime():
    async def run():
        builds = []

        class SometimesFails(ChatWorkflow):
            async def _respond(self, request):
                if request.message == "fail":
                    raise RuntimeError("turn failed")
                return ChatResponse(reply="ok")

        @asynccontextmanager
        async def factory(context):
            builds.append(context)
            yield SometimesFails(context)

        async with WorkflowFacade(factory) as facade:
            with pytest.raises(RuntimeError, match="turn failed"):
                await facade.respond(client(), turn(message="fail"))
            assert (await facade.respond(client(), turn())).reply == "ok"
            assert len(builds) == 1
            assert facade._conversations == {}

    asyncio.run(run())


def test_shutdown_waits_for_active_leases_and_refuses_new_work():
    async def run():
        factory, built, closed = factory_with_history()
        facade = WorkflowFacade(factory)
        async with facade.cache.acquire(client()):
            closing = asyncio.create_task(facade.aclose())
            await asyncio.sleep(0)
            assert not closing.done()
            assert closed == []
            with pytest.raises(RuntimeCacheClosed):
                await facade.warm(client("other"))
        await asyncio.wait_for(closing, timeout=5)
        await facade.aclose()
        assert closed == built

    asyncio.run(run())


def test_shutdown_cancels_an_orphaned_build_and_runs_factory_cleanup():
    async def run():
        started, cleaned = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def factory(context):
            try:
                started.set()
                await asyncio.Event().wait()
                yield CountingWorkflow(context, InMemorySaver())
            finally:
                cleaned.set()

        facade = WorkflowFacade(factory)
        waiting = asyncio.create_task(facade.warm(client()))
        await asyncio.wait_for(started.wait(), timeout=5)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await asyncio.wait_for(facade.aclose(), timeout=5)
        assert cleaned.is_set()

    asyncio.run(run())


def test_client_mismatch_is_rejected_before_factory_and_release_metadata_cannot_change():
    async def run():
        factory, built, _ = factory_with_history()
        async with WorkflowFacade(factory) as facade:
            with pytest.raises(ValueError, match="server-resolved"):
                await facade.respond(client(), turn(client("other")))
            assert built == []
            await facade.warm(client())
            changed = ClientContext(client_id="chef_001", release_id="v1", display_name="Changed")
            with pytest.raises(ValueError, match="new release ID"):
                await facade.warm(changed)

    asyncio.run(run())


def test_api_and_cli_can_use_the_facade_through_the_existing_workflow_contract(monkeypatch, capsys):
    factory, built, closed = factory_with_history()

    @asynccontextmanager
    async def lifespan(app):
        async with WorkflowFacade(factory) as facade:
            app.state.workflow = facade.for_client(client())
            yield

    application = api.create_http_app()
    application.router.lifespan_context = lifespan
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)
    with TestClient(application) as browser:
        assert browser.post("/chat", json={"message": "Hi"}).json()["reply"] == "1: Hi"
        assert browser.post("/chat", json={"message": "Again"}).json()["reply"] == "2: Again"
        assert len(built) == 1
    assert closed == built

    async def terminal():
        async with WorkflowFacade(factory) as facade:
            return await ChatCommand(lambda: facade.for_client(client())).run(Namespace(user_id="terminal"))

    messages = iter(["Hi", "Again", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(messages))
    assert asyncio.run(terminal()) == 0
    assert "2: Again" in capsys.readouterr().out
    assert len(built) == len(closed) == 2


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_invalid_cache_capacity_is_rejected(capacity):
    factory, _, _ = factory_with_history()
    with pytest.raises(ValueError, match="positive integer"):
        WorkflowFacade(factory, max_cached_workflows=capacity)
