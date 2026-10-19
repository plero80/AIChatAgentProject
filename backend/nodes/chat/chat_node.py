from backend.tenancy import TenantContext, load_tenant

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from backend.graphs.graph_state import GraphState


class ChatNode:
    def __init__(self, llm: BaseChatModel, *, tenant: TenantContext | None = None):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.llm = llm

    async def __call__(self, state: GraphState) -> dict:
        history = state.get("messages") or []
        prompt_messages = [
            (
                "system",
                self.tenant.render("chat"),
            ),
            *history[-10:],
        ]
        # Ensure the latest user message is present even if history is empty
        if not any(isinstance(m, HumanMessage) for m in history[-2:]):
            prompt_messages.append(HumanMessage(content=state["message"]))

        reply = await self.llm.ainvoke(prompt_messages)
        text = str(reply.text)

        return {
            "result": {
                "mode": "chat",
                "text": text,
            },
            "messages": [AIMessage(content=text)],
        }
