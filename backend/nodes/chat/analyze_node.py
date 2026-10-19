from backend.services.contracts import BaseMessageAnalyzer
from backend.graphs.graph_state import GraphState


class AnalyzeNode:
    def __init__(self, analyzer: BaseMessageAnalyzer):
        self.analyzer = analyzer

    async def __call__(self, state: GraphState) -> dict:
        history = state.get("messages") or []
        recent = []
        for message in history[-6:]:
            role = getattr(message, "type", "user")
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                content = str(content)
            recent.append(f"{role}: {content}")

        analysis_input = state["message"]
        if recent:
            analysis_input = (
                "Recent conversation:\n"
                + "\n".join(recent)
                + "\n\nCurrent user message:\n"
                + state["message"]
            )

        return {
            "analysis": await self.analyzer.analyze(
                analysis_input,
                current_user_message=state["message"],
            ),
        }
