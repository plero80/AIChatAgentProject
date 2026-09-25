import asyncio
import logging

from langchain_core.messages import AIMessage

from Graph.GraphState import GraphState
from Models.RequestValidation import RequestValidation
from Services.RequestValidator import RequestValidator


logger = logging.getLogger("alona.guard")

FALLBACK_REASON = "אני יכולה לעזור רק עם מתכונים, בישול ותזונה של אלונה."


class GuardNode:
    """Parent step: validate and run the branch together.

    The branch never writes to the user. This node does.
    A rejected request cancels the branch and returns validation.reason.
    """

    def __init__(
        self,
        validator: RequestValidator,
        chat_node,
        recipe_node,
        dietitian_node,
    ):
        self.validator = validator
        self.branches = {
            "chat": chat_node,
            "recipe": recipe_node,
            "dietitian": dietitian_node,
        }

    async def __call__(self, state: GraphState) -> dict:
        branch = self.branches[self._branch_name(state)]
        validate_task = asyncio.create_task(
            self.validator.validate(
                state["message"],
                history=self._history_text(state),
            )
        )
        branch_task = asyncio.create_task(branch(state))

        try:
            try:
                validation = await validate_task
            except Exception:
                logger.exception("Request validation failed; continuing the main flow")
                return await branch_task

            if validation is None or validation.legitimate:
                update = dict(await branch_task)
                if validation is not None:
                    update["validation"] = validation
                return update

            await _discard(branch_task)
            reason = (validation.reason or "").strip() or FALLBACK_REASON
            logger.info("Rejected request")
            return {
                "validation": validation,
                "result": None,
                "final_response": reason,
                "messages": [AIMessage(content=reason)],
            }
        except asyncio.CancelledError:
            validate_task.cancel()
            branch_task.cancel()
            raise

    def _branch_name(self, state: GraphState) -> str:
        analysis = state.get("analysis")
        if analysis is None:
            return "chat"
        if analysis.intent == "food":
            return "recipe"
        if analysis.intent == "dietitian_escort":
            return "dietitian"
        return "chat"

    def _history_text(self, state: GraphState) -> str:
        history = state.get("messages") or []
        lines = []
        for message in history[-6:]:
            role = getattr(message, "type", "user")
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                content = str(content)
            lines.append(f"{role}: {content}")
        return "\n".join(lines)


async def _discard(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        return
