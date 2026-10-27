"""A bounded, process-local cache of reusable workflows, not conversation state."""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
import logging

from backend.clients.context import ClientContext
from backend.workflows.contracts import ChatWorkflow


logger = logging.getLogger(__name__)
WorkflowFactory = Callable[[ClientContext], AbstractAsyncContextManager[ChatWorkflow]]
RuntimeKey = tuple[str, str]


class RuntimeCacheClosed(RuntimeError):
    """The application has stopped admitting workflow acquisitions."""


@dataclass
class _Runtime:
    workflow: ChatWorkflow
    resources: AsyncExitStack


@dataclass
class _Entry:
    client: ClientContext
    build: asyncio.Task[_Runtime]
    users: int = 0


class WorkflowRuntimeCache:
    """Build once per (client, immutable release); lease entries during execution.

    The factory is an async context manager. It owns any per-runtime resources
    and closes them on eviction/shutdown. Application-owned pools/checkpointers
    must live outside that factory so an eviction cannot delete conversation state.
    All operations belong to one application event loop.
    """

    def __init__(self, factory: WorkflowFactory, *, max_entries: int = 16):
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self.factory = factory
        self.max_entries = max_entries
        self._entries: OrderedDict[RuntimeKey, _Entry] = OrderedDict()
        self._condition = asyncio.Condition()
        self._loop = None
        self._closed = False
        self._close_task = None
        self._cleanup_tasks: set[asyncio.Task] = set()

    @property
    def keys(self) -> tuple[RuntimeKey, ...]:
        """A diagnostic snapshot, ordered from least to most recently used."""
        return tuple(self._entries)

    def _check_loop(self):
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
        elif self._loop is not current:
            raise RuntimeError("Create one workflow cache per application event loop")

    @asynccontextmanager
    async def acquire(self, client: ClientContext):
        self._check_loop()
        entry = await self._reserve(client)
        try:
            # Cancelling one waiting request must not cancel a shared construction.
            runtime = await asyncio.shield(entry.build)
            yield runtime.workflow
        finally:
            async with self._condition:
                entry.users -= 1
                key = (client.client_id, client.release_id)
                if entry.users == 0 and entry.build.done() and self._failed(entry.build):
                    if self._entries.get(key) is entry:
                        del self._entries[key]
                self._condition.notify_all()

    async def warm(self, client: ClientContext) -> None:
        async with self.acquire(client):
            pass

    @staticmethod
    def _failed(task):
        return task.cancelled() or task.exception() is not None

    async def _reserve(self, client):
        key = (client.client_id, client.release_id)
        while True:
            async with self._condition:
                if self._closed:
                    raise RuntimeCacheClosed("Workflow cache is closed")
                entry = self._entries.get(key)
                if entry is not None and entry.users == 0 and entry.build.done() and self._failed(entry.build):
                    del self._entries[key]
                    entry = None
                if entry is not None:
                    if entry.client != client:
                        raise ValueError("Client metadata changed without a new release ID")
                    entry.users += 1
                    self._entries.move_to_end(key)
                    logger.debug("Workflow cache hit client=%s release=%s", *key)
                    return entry
                if len(self._entries) < self.max_entries:
                    task = asyncio.create_task(self._build(client))
                    # Retrieve failures even when every waiting caller cancels.
                    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
                    entry = _Entry(client=client, build=task, users=1)
                    self._entries[key] = entry
                    logger.info("Workflow cache miss; building client=%s release=%s", *key)
                    return entry

                # Never dispose a workflow while a request is using/waiting for it.
                victim_key = next((
                    candidate for candidate, value in self._entries.items()
                    if value.users == 0 and value.build.done()
                ), None)
                if victim_key is None:
                    await self._condition.wait()
                    continue
                victim = self._entries.pop(victim_key)
                cleanup = self._schedule_cleanup(victim)
                self._condition.notify_all()
            # Close outside the lock so independent clients can make progress.
            await asyncio.shield(cleanup)

    async def _build(self, client):
        resources = AsyncExitStack()
        try:
            workflow = await resources.enter_async_context(self.factory(client))
            if not isinstance(workflow, ChatWorkflow):
                raise TypeError("Workflow factory must yield a ChatWorkflow")
            if workflow.client != client:
                raise ValueError("Built workflow does not match the resolved client/release")
            return _Runtime(workflow, resources)
        except BaseException:
            await resources.aclose()
            raise
        finally:
            async with self._condition:
                self._condition.notify_all()

    def _schedule_cleanup(self, entry):
        async def dispose():
            if not self._failed(entry.build):
                await entry.build.result().resources.aclose()

        task = asyncio.create_task(dispose())
        self._cleanup_tasks.add(task)

        def completed(done):
            self._cleanup_tasks.discard(done)
            if done.cancelled():
                logger.error("Workflow resource cleanup was cancelled")
            elif (error := done.exception()) is not None:
                logger.error("Workflow resource cleanup failed: %s", type(error).__name__)

        task.add_done_callback(completed)
        return task

    async def aclose(self) -> None:
        """Stop admission, drain active leases, then close cached resources."""
        self._check_loop()
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_all())
        await asyncio.shield(self._close_task)

    async def _close_all(self):
        async with self._condition:
            self._condition.notify_all()
            while any(entry.users for entry in self._entries.values()):
                await self._condition.wait()
            entries = list(self._entries.values())
            self._entries.clear()
        # Pending builds now have no callers. Cancel and await their cleanup.
        for entry in entries:
            if not entry.build.done():
                entry.build.cancel()
        await asyncio.gather(*(entry.build for entry in entries), return_exceptions=True)
        pending = list(self._cleanup_tasks)
        pending.extend(self._schedule_cleanup(entry) for entry in entries)
        results = await asyncio.gather(*pending, return_exceptions=True)
        errors = [error for error in results if isinstance(error, BaseException)]
        if errors:
            raise BaseExceptionGroup("Workflow resource cleanup failed", errors)
