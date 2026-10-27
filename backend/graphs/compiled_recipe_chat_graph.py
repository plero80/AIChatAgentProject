"""Recipe compatibility binding for the generic JSON compiler."""

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from backend.config.workflows import WorkflowConfigurationError, load_workflow_spec
from backend.graphs.recipe_chat_graph import RecipeChatGraph
from backend.models.message_analysis import MessageAnalysis
from backend.paths import CONFIG_DIR
from backend.workflows.compiler import compile_workflow
from backend.workflows.build_trace import GraphBuildTrace
from backend.workflows.registry import NodeDefinition, RouterDefinition, StateField, WorkflowRegistry


def validate_recipe_policy(spec):
    """Check domain routing; admission is enforced outside all graph definitions."""
    routes = {route.source: route for route in spec.routes}
    kinds = {node.id: node.kind for node in spec.nodes}
    analyses = [node.id for node in spec.nodes if node.kind == "recipe_analysis"]
    if not analyses:
        raise WorkflowConfigurationError("Recipe workflow requires analysis and intent routing")
    for analysis_id in analyses:
        route = routes.get(analysis_id)
        if (
            route is None or route.router != "recipe_intent"
            or set(route.paths) != {"chat", "recipe", "dietitian"}
            or any(kinds.get(route.paths[outcome]) != kind for outcome, kind in {
                "chat": "recipe_chat", "recipe": "recipe_execute", "dietitian": "recipe_contact",
            }.items())
        ):
            raise WorkflowConfigurationError(
                f"Recipe analysis {analysis_id!r} must route intents to chat, recipe, and contact nodes"
            )


class CompiledRecipeChatGraph(RecipeChatGraph):
    """Reuse recipe dependencies and conversation semantics with topology from JSON."""

    def build_graph(self):
        with GraphBuildTrace(workflow="recipe_chat", source="json", client_id=self.tenant.dietitian_id) as trace:
            self.build_trace_path = trace.path
            return self._build_json_graph(trace)

    def _build_json_graph(self, trace):
        trace.stage("load", path=str(CONFIG_DIR / "workflows" / "recipe_chat.json"))
        spec = load_workflow_spec(CONFIG_DIR / "workflows" / "recipe_chat.json")
        trace.stage("recipe_policy")
        validate_recipe_policy(spec)
        trace.record("recipe_policy_validated")
        text = StateField(str)
        messages = StateField(list[BaseMessage], reducer=add_messages)
        analysis = StateField(MessageAnalysis)
        result = StateField(dict | None)
        reply = StateField(str | None)
        blocks = StateField(list[dict] | None)
        registry = WorkflowRegistry()
        registry.register_node("recipe_analysis", NodeDefinition(
            factory=lambda: self.nodes.analysis,
            inputs={"message": text, "messages": messages},
            outputs={"analysis": analysis},
        ))

        registry.register_node("recipe_chat", NodeDefinition(
            factory=lambda: self.nodes.chat,
            inputs={"message": text, "messages": messages},
            outputs={"result": result, "messages": messages},
        ))
        registry.register_node("recipe_execute", NodeDefinition(
            factory=lambda: self.nodes.recipe,
            inputs={"message": text, "analysis": analysis},
            outputs={"result": result},
        ))
        registry.register_node("recipe_contact", NodeDefinition(
            factory=lambda: self.nodes.dietitian,
            inputs={},
            outputs={"result": result},
        ))
        registry.register_node("recipe_response", NodeDefinition(
            factory=lambda: self.nodes.response,
            inputs={"message": text, "messages": messages, "result": result},
            outputs={"final_response": reply, "response_blocks": blocks, "messages": messages},
        ))
        registry.register_router("recipe_intent", RouterDefinition(
            route=self.route_after_analysis,
            inputs={"analysis": analysis},
            outcomes=frozenset({"chat", "recipe", "dietitian"}),
        ))
        return compile_workflow(
            spec,
            registry,
            initial_fields={
                "message": text, "messages": messages, "user_id": text, "dietitian_id": text,
            },
            protected_fields=frozenset({"message", "user_id", "dietitian_id"}),
            required_outputs=frozenset({"final_response", "response_blocks"}),
            checkpointer=self.memory,
            build_trace=trace,
        )

    async def arun(self, request):
        if request.dietitian_id != self.tenant.dietitian_id:
            raise ValueError("Request dietitian does not match this graph deployment")
        return await self.graph.ainvoke(
            {
                "user_id": request.user_id,
                "dietitian_id": request.dietitian_id,
                "message": request.message,
                "messages": [HumanMessage(content=request.message)],
            },
            config={"configurable": {"thread_id": request.thread_id}},
        )
