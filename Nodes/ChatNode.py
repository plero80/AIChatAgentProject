from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from Graph.GraphState import GraphState


class ChatNode:
    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    def __call__(self, state: GraphState) -> dict:
        history = state.get("messages") or []
        prompt_messages = [
            (
                "system",
                """
                You are AlonaAI, a friendly Instagram nutrition assistant for Alona Eckrling.
                Answer warmly and briefly. Match the user's language.
                Do NOT invent recipes here. Do NOT claim you lack a recipe database.
                If the user wants a recipe, keep it short and let the recipe workflow handle it.
                """,
            ),
            *history[-10:],
        ]
        # Ensure the latest user message is present even if history is empty
        if not any(isinstance(m, HumanMessage) for m in history[-2:]):
            prompt_messages.append(HumanMessage(content=state["message"]))

        reply = self.llm.invoke(prompt_messages)
        text = reply.content if isinstance(reply.content, str) else str(reply.content)

        return {
            "result": {
                "mode": "chat",
                "text": text,
            },
            "messages": [AIMessage(content=text)],
        }
