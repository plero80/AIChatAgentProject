"""Demonstrate cached JSON graphs and independent conversation checkpoints.

    python -m backend.workflows.facade_example

No API keys or database are needed. InMemorySaver lives outside the cache here;
the API can supply its application-owned PostgreSQL checkpointer instead.
"""

import asyncio
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from backend.clients.context import ClientContext
from backend.models.chat_response import ChatResponse
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.workflows.example import build_example
from backend.workflows.facade import WorkflowFacade


class WordCountWorkflow(ChatWorkflow):
    def __init__(self, client, checkpointer):
        super().__init__(client)
        self.graph = build_example(checkpointer=checkpointer)

    async def _respond(self, request):
        result = await self.graph.ainvoke(
            {"message": request.message},
            {"configurable": {"thread_id": request.thread_id}},
        )
        return ChatResponse(reply=result["reply"])


async def main():
    checkpointer = InMemorySaver(serde=JsonPlusSerializer(
        allowed_msgpack_modules=[("backend.workflows.example", "TextAnalysis")],
    ))
    builds = 0

    @asynccontextmanager
    async def factory(client):
        nonlocal builds
        workflow = WordCountWorkflow(client, checkpointer)
        builds += 1
        print(f"Build {builds}: {client.client_id}/{client.release_id}")
        print("Build trace:", workflow.graph.build_trace_path)
        try:
            yield workflow
        finally:
            # This example owns no external connections. A real factory closes
            # its own per-runtime resources here, keeping shared persistence open.
            print(f"Release runtime: {client.client_id}/{client.release_id}")

    client = ClientContext(client_id="chef_demo", display_name="Chef", release_id="v1")
    other = ClientContext(client_id="other_demo", display_name="Other", release_id="v1")
    async with WorkflowFacade(factory, max_cached_workflows=1) as facade:
        workflow = facade.for_client(client)
        for conversation, message in (("alice", "Hello chef"), ("bob", "Show me a recipe")):
            response = await workflow.respond(ConversationRequest(
                client_id=client.client_id, release_id=client.release_id,
                conversation_id=conversation, message=message,
            ))
            print(f"{conversation}: {response.reply}")
        print("Builds after two users:", builds)
        print("Runtime cache keys:", facade.cache.keys)

        # Evict the first graph. Its conversation checkpoints still exist.
        await facade.warm(other)
        checkpoint = await checkpointer.aget_tuple({
            "configurable": {"thread_id": f"{client.client_id}:alice"},
        })
        print("Alice's checkpoint survives eviction:", checkpoint is not None)

        response = await workflow.respond(ConversationRequest(
            client_id=client.client_id, release_id=client.release_id,
            conversation_id="alice", message="Hello again",
        ))
        print("alice:", response.reply)
        print("Builds after eviction and reuse:", builds)


if __name__ == "__main__":
    asyncio.run(main())
