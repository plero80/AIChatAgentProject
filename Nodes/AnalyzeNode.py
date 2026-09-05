from Services.MessageAnalyzer import MessageAnalyzer
from Graph.GraphState import GraphState


class AnalyzeNode:
    def __init__(self, analyzer: MessageAnalyzer):
        self.analyzer = analyzer

    def __call__(self, state: GraphState) -> dict:
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
            "analysis": self.analyzer.analyze(analysis_input),
        }
