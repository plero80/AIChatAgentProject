import asyncio

from langchain_core.messages import AIMessage

from Graph.RecipeChatGraph import RecipeChatGraph
from Models.MessageAnalysis import MessageAnalysis
from Models.RequestValidation import RequestValidation
from Nodes.GuardNode import GuardNode


def test_persisted_recipe_blocks_and_rejection_do_not_leak_into_later_turns():
    class Analyze:
        async def __call__(self, state):
            # Every turn must begin with presentation/routing state cleared.
            for key in ("analysis", "validation", "result", "final_response", "response_blocks"):
                assert state.get(key) is None
            return {"analysis": MessageAnalysis(
                intent="chat", recipe_mode="none", ingredients=[], preferences=[], confidence=1
            )}

    class Validator:
        async def validate(self, message, history=""):
            if message == "reject":
                return RequestValidation(legitimate=False, reason="לא קשור למטבח")
            if message == "follow-up":
                raise RuntimeError("validation temporarily unavailable")
            return RequestValidation(legitimate=True)

    class Branch:
        async def __call__(self, state):
            return {"result": {"mode": "chat", "text": state["message"]}}

    class Response:
        async def __call__(self, state):
            text = state["result"]["text"]
            blocks = (
                [{"type": "recipe", "recipe": {"id": "tofu", "image_url": "/recipe-images/tofu.jpg"}}]
                if text == "recipe" else [{"type": "text", "text": text}]
            )
            return {"final_response": text, "response_blocks": blocks, "messages": [AIMessage(content=text)]}

    branch = Branch()
    graph = RecipeChatGraph(
        analyze_node=Analyze(),
        guard_node=GuardNode(Validator(), branch, branch, branch),
        response_node=Response(),
    )

    async def conversation():
        first = await graph.arun("recipe", "one-session")
        assert first["response_blocks"][0]["type"] == "recipe"

        rejected = await graph.arun("reject", "one-session")
        assert rejected["response_blocks"] == [{"type": "text", "text": "לא קשור למטבח"}]
        assert "tofu.jpg" not in str(rejected["response_blocks"])

        final = await graph.arun("follow-up", "one-session")
        assert final["final_response"] == "follow-up"
        assert final["response_blocks"] == [{"type": "text", "text": "follow-up"}]
        assert final["validation"] is None
        assert any(message.content == "recipe" for message in final["messages"])

    asyncio.run(conversation())
