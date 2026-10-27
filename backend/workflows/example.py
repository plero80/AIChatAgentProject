"""Run a small JSON workflow without API keys or a database.

    python -m backend.workflows.example
"""

import asyncio

from pydantic import BaseModel, ConfigDict

from backend.config.workflows import load_workflow_spec
from backend.paths import CONFIG_DIR
from backend.workflows.compiler import compile_workflow
from backend.workflows.registry import NodeDefinition, StateField, WorkflowRegistry


class TextAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word_count: int


class WordCounter:
    async def analyze(self, message: str) -> TextAnalysis:
        return TextAnalysis(word_count=len(message.split()))


def build_example(*, checkpointer=None):
    service = WordCounter()

    async def analyze(state):
        return {"analysis": await service.analyze(state["message"])}

    async def respond(state):
        return {"reply": f"Your message contains {state['analysis'].word_count} words."}

    registry = WorkflowRegistry()
    analysis = StateField(TextAnalysis)
    registry.register_node("word_analysis", NodeDefinition(
        factory=lambda: analyze,
        inputs={"message": StateField(str)},
        outputs={"analysis": analysis},
    ))
    registry.register_node("word_reply", NodeDefinition(
        factory=lambda: respond,
        inputs={"analysis": analysis},
        outputs={"reply": StateField(str)},
    ))
    return compile_workflow(
        load_workflow_spec(CONFIG_DIR / "workflows" / "word_count.example.json"),
        registry,
        initial_fields={"message": StateField(str)},
        protected_fields=frozenset({"message"}),
        required_outputs=frozenset({"reply"}),
        checkpointer=checkpointer,
    )


async def main():
    workflow = build_example()
    result = await workflow.ainvoke({"message": "Show me a recipe"})
    print("Derived fields:", ", ".join(workflow.fields))
    print(result["reply"])
    print("Build trace:", workflow.build_trace_path)


if __name__ == "__main__":
    asyncio.run(main())
