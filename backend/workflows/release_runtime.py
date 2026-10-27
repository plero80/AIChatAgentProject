"""Bind published JSON to trusted constructors, then compile a guarded runtime."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from string import Template
from types import MappingProxyType

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from backend.clients.releases import ReleaseSpec
from backend.config.workflows import WorkflowConfigurationError
from backend.config.images import validate_image_config
from backend.factories.service_factories import ServiceFactories
from backend.graphs.compiled_recipe_chat_graph import validate_recipe_policy
from backend.graphs.recipe_chat_graph import RecipeChatGraph
from backend.guards.history import CheckpointHistoryLoader
from backend.guards.request_guard import RequestGuard
from backend.models.chat_response import ChatResponse
from backend.models.message_analysis import MessageAnalysis
from backend.nodes.chat.analyze_node import AnalyzeNode
from backend.nodes.chat.chat_node import ChatNode
from backend.nodes.chat.dietitian_node import DietitianNode
from backend.nodes.chat.recipe_node import RecipeNode
from backend.nodes.chat.response_node import ResponseNode
from backend.paths import DATA_DIR
from backend.services.chat.recipe_presentation_service import build_text_response
from backend.services.contracts import BaseRecipeSearchService
from backend.tenancy import DietitianProfile, PROMPT_ARGUMENTS, PROFILE_ARGUMENTS, TenantContext
from backend.workflows.build_trace import GraphBuildTrace
from backend.workflows.compiler import compile_workflow
from backend.workflows.contracts import ChatWorkflow
from backend.workflows.guarded import GuardedWorkflow
from backend.workflows.image_input import ImageInputProcessor, ImageInputUnsupported
from backend.workflows.registry import NodeDefinition, RouterDefinition, StateField, WorkflowRegistry
from backend.workflows.validation import validate_workflow


# node kind -> dependency slot -> (factory contract, uses model, prompt slots).
# Extending capabilities means adding a reviewed Python contract here.
BINDING_CONTRACTS = {
    "recipe_analysis": {"analyzer": ("message_analyzer", True, {"analyze"})},
    "recipe_chat": {"chat": ("chat_model", True, {"chat"})},
    "recipe_execute": {
        "search": ("recipe_search", False, {"recipe_not_found"}),
        "generation": ("recipe_generation", True, {"generate_original", "generate_inspired"}),
    },
    "recipe_contact": {"contact": ("dietitian", False, {"dietitian_contact"})},
    "recipe_response": {
        "writer": ("chat_model", True, {"general_response", "recipe_presentation"}),
        "presentation": ("recipe_presentation", False, set()),
    },
    "text_reply": {"writer": ("chat_model", True, {"system"})},
}

TEXT = StateField(str)
MESSAGES = StateField(list[BaseMessage], reducer=add_messages)
ANALYSIS = StateField(MessageAnalysis)
RESULT = StateField(dict | None)
REPLY = StateField(str | None)
BLOCKS = StateField(list[dict] | None)
INITIAL_FIELDS = {"message": TEXT, "messages": MESSAGES, "user_id": TEXT, "client_id": TEXT}
PROTECTED_FIELDS = frozenset({"message", "user_id", "client_id"})
REQUIRED_OUTPUTS = frozenset({"final_response", "response_blocks"})


def release_registry(build_node=None):
    registry = WorkflowRegistry()
    definitions = {
        "recipe_analysis": ({"message": TEXT, "messages": MESSAGES}, {"analysis": ANALYSIS}),
        "recipe_chat": ({"message": TEXT, "messages": MESSAGES}, {"result": RESULT, "messages": MESSAGES}),
        "recipe_execute": ({"message": TEXT, "analysis": ANALYSIS}, {"result": RESULT}),
        "recipe_contact": ({}, {"result": RESULT}),
        "recipe_response": (
            {"message": TEXT, "messages": MESSAGES, "result": RESULT},
            {"final_response": REPLY, "response_blocks": BLOCKS, "messages": MESSAGES},
        ),
        "text_reply": (
            {"message": TEXT, "messages": MESSAGES},
            {"final_response": REPLY, "response_blocks": BLOCKS, "messages": MESSAGES},
        ),
    }

    def unbound(*args):
        raise RuntimeError("Validation registry cannot build runtime nodes")

    for kind, (inputs, outputs) in definitions.items():
        registry.register_node(kind, NodeDefinition(
            factory=unbound, inputs=inputs, outputs=outputs, binding_factory=build_node or unbound,
        ))
    registry.register_router("recipe_intent", RouterDefinition(
        route=RecipeChatGraph.route_after_analysis, inputs={"analysis": ANALYSIS},
        outcomes=frozenset({"chat", "recipe", "dietitian"}),
    ))
    return registry


def validate_release(spec: ReleaseSpec, *, factories=None):
    """Pure validation: no provider clients, database connections or node construction."""
    factories = factories or ServiceFactories()
    factories.guard_evaluator.validate(spec.guard.evaluator)
    validate_image_config(spec.image_recognition, factories)
    for profile in spec.models.values():
        factories.chat_model.validate(profile.provider)
    for node in spec.workflow.nodes:
        contract = BINDING_CONTRACTS.get(node.kind)
        if contract is None or node.bindings.keys() != contract.keys():
            raise WorkflowConfigurationError(f"Node {node.id!r} bindings must match its registered contract")
        for slot, (factory_name, needs_model, prompt_slots) in contract.items():
            binding = node.bindings[slot]
            if factory_name == "chat_model":
                if binding.implementation != "llm":
                    raise WorkflowConfigurationError(f"Node {node.id!r}: writer implementation must be llm")
            else:
                getattr(factories, factory_name).validate(binding.implementation)
            if needs_model != (binding.model is not None):
                raise WorkflowConfigurationError(f"Node {node.id!r}: incorrect model binding for {slot}")
            if binding.model is not None and binding.model not in spec.models:
                raise WorkflowConfigurationError(f"Node {node.id!r}: unknown model profile")
            if binding.prompts.keys() != prompt_slots:
                raise WorkflowConfigurationError(f"Node {node.id!r}: incorrect prompt slots for {slot}")
            for prompt_slot, reference in binding.prompts.items():
                if reference not in spec.prompts:
                    raise WorkflowConfigurationError(f"Node {node.id!r}: unknown prompt reference")
                allowed = PROFILE_ARGUMENTS | PROMPT_ARGUMENTS.get(prompt_slot, set())
                if set(Template(spec.prompts[reference].text).get_identifiers()) - allowed:
                    raise WorkflowConfigurationError(f"Node {node.id!r}: unsupported prompt variables")
    if any(node.kind == "recipe_execute" for node in spec.workflow.nodes) and not spec.embedding_model:
        raise WorkflowConfigurationError("Recipe search requires a pinned embedding_model")
    if any(node.kind == "recipe_analysis" for node in spec.workflow.nodes):
        validate_recipe_policy(spec.workflow)
    validate_workflow(
        spec.workflow, release_registry(), initial_fields=INITIAL_FIELDS,
        protected_fields=PROTECTED_FIELDS, required_outputs=REQUIRED_OUTPUTS,
    )


def image_directory(client_id):
    # Retain existing assets without exposing arbitrary filesystem paths in JSON.
    return DATA_DIR / "dietitians" / client_id / "recipe_images"


class ScopedRecipeSearch(BaseRecipeSearchService):
    def __init__(self, service, slug):
        self.service = service
        self.prefix = f"/api/clients/{slug}/recipe-images/"

    async def find_best_recipes(self, user_text, plan, top_k=5):
        recipes = await self.service.find_best_recipes(user_text, plan, top_k)
        return [recipe.model_copy(update={"image_url": self.prefix + recipe.image_url[len('/recipe-images/'):]})
                if recipe.image_url and recipe.image_url.startswith("/recipe-images/") else recipe
                for recipe in recipes]


async def recipe_dependencies(release, resources):
    """Only recipe workflows open recipe storage. Runtime startup never migrates it."""
    from langchain_openai import OpenAIEmbeddings
    from backend.config.settings import settings
    from backend.factories.provider_resources import openai_transport_options
    from backend.persistence.database import Database
    from backend.persistence.repositories.recipe_repository import RecipeRepository
    from backend.persistence.repositories.recipe_ingredient_repository import RecipeIngredientRepository
    from backend.persistence.repositories.recipe_embedding_repository import RecipeEmbeddingRepository
    from backend.persistence.schema import SCHEMA_VERSION

    db = Database(host=settings.DB_HOST, port=settings.DB_PORT, dbname=settings.DB_NAME,
                  user=settings.DB_USER, password=settings.DB_PASSWORD,
                  min_size=0, max_size=settings.DB_POOL_MAX, dietitian_id=release.client.client_id)
    resources.push_async_callback(asyncio.to_thread, db.close)
    version = await asyncio.to_thread(db.fetch_one, "SELECT MAX(version) AS version FROM recipe_schema_versions")
    if version["version"] != SCHEMA_VERSION:
        raise RuntimeError("Prepare the recipe schema before publishing recipe workflows")
    embeddings = OpenAIEmbeddings(model=release.spec.embedding_model, **openai_transport_options(resources))
    return {
        "recipe_repository": RecipeRepository(db, release.client.client_id),
        "ingredient_repository": RecipeIngredientRepository(db, release.client.client_id),
        "embedding_repository": RecipeEmbeddingRepository(db, release.client.client_id),
        "embedding_model": embeddings,
    }


class ReleaseNodeBuilder:
    def __init__(self, release, factories, resources, search_dependencies):
        self.release = release
        self.spec = release.spec
        self.factories = factories
        self.resources = resources
        self.search_dependencies = search_dependencies
        self.models = {}

    def model(self, binding):
        name = binding.model
        if name not in self.models:
            profile = self.spec.models[name]
            self.models[name] = self.factories.chat_model.create(
                profile.provider, profile=profile, resources=self.resources,
            )
        return self.models[name]

    def tenant(self, *bindings):
        profile = DietitianProfile.model_validate({
            **self.spec.profile.model_dump(mode="json"), "dietitian_id": self.spec.client_id,
        })
        prompts = {slot: self.spec.prompts[reference].text
                   for binding in bindings for slot, reference in binding.prompts.items()}
        return TenantContext(profile, MappingProxyType(prompts), image_directory(self.spec.client_id))

    def service(self, factory_name, binding, **dependencies):
        return getattr(self.factories, factory_name).create(
            binding.implementation, resources=self.resources, **dependencies,
        )

    def __call__(self, node):
        bindings = node.bindings
        if node.kind == "recipe_analysis":
            binding = bindings["analyzer"]
            return AnalyzeNode(analyzer=self.service("message_analyzer", binding,
                               llm=self.model(binding), tenant=self.tenant(binding)))
        if node.kind == "recipe_chat":
            binding = bindings["chat"]
            return ChatNode(llm=self.model(binding), tenant=self.tenant(binding))
        if node.kind == "recipe_execute":
            search, generation = bindings["search"], bindings["generation"]
            return RecipeNode(
                self.service("recipe_generation", generation, llm=self.model(generation), tenant=self.tenant(generation)),
                ScopedRecipeSearch(self.service("recipe_search", search, **self.search_dependencies), self.release.client.slug),
                tenant=self.tenant(search),
            )
        if node.kind == "recipe_contact":
            binding = bindings["contact"]
            tenant = self.tenant(binding)
            return DietitianNode(self.service("dietitian", binding, tenant=tenant), tenant=tenant)
        if node.kind == "recipe_response":
            writer = bindings["writer"]
            return ResponseNode(self.model(writer), self.service("recipe_presentation", bindings["presentation"]),
                                tenant=self.tenant(writer))
        if node.kind == "text_reply":
            binding = bindings["writer"]
            model = self.model(binding)
            template = self.spec.prompts[binding.prompts["system"]].text
            system = Template(template).substitute({key: getattr(self.spec.profile, key) for key in PROFILE_ARGUMENTS})

            async def respond(state):
                answer = await model.ainvoke([("system", system), *state["messages"][-10:]])
                response = build_text_response(str(answer.text))
                return {"final_response": response.reply,
                        "response_blocks": [block.model_dump(mode="json") for block in response.blocks],
                        "messages": [AIMessage(content=response.reply)]}

            return respond
        raise WorkflowConfigurationError("Unregistered node kind")


class CompiledReleaseWorkflow(ChatWorkflow):
    def __init__(self, client, graph):
        super().__init__(client)
        self.graph = graph

    async def _respond(self, request):
        if request.images:
            raise ImageInputUnsupported("Photos must be processed by the guarded workflow first")
        result = await self.graph.ainvoke(
            {"message": request.message, "messages": [HumanMessage(content=request.message)],
             "user_id": request.conversation_id, "client_id": request.client_id},
            config={"configurable": {"thread_id": request.thread_id}},
        )
        return ChatResponse(reply=result["final_response"], blocks=result["response_blocks"])


def create_release_workflow_factory(catalog, checkpointer, *, factories=None, dependencies=recipe_dependencies):
    if checkpointer is None:
        raise ValueError("Published workflows require a shared checkpointer")
    factories = factories or ServiceFactories()

    @asynccontextmanager
    async def build(client):
        release = await catalog.get_release(client.client_id, client.release_id)
        if release.context != client:
            raise ValueError("Resolved context differs from the published release")
        validate_release(release.spec, factories=factories)
        async with AsyncExitStack() as resources:
            search_dependencies = (
                await dependencies(release, resources)
                if any(node.kind == "recipe_execute" for node in release.spec.workflow.nodes) else {}
            )
            node_builder = ReleaseNodeBuilder(release, factories, resources, search_dependencies)
            with GraphBuildTrace(workflow=release.spec.workflow.name, source="database", client_id=client.client_id) as trace:
                trace.record("release_selected", release_id=release.release_id, version=release.version,
                             fingerprint=release.spec.fingerprint())
                for node in release.spec.workflow.nodes:
                    trace.record("bindings_selected", node_id=node.id,
                                 bindings={slot: binding.model_dump() for slot, binding in node.bindings.items()})
                graph = compile_workflow(
                    release.spec.workflow, release_registry(node_builder), initial_fields=INITIAL_FIELDS,
                    protected_fields=PROTECTED_FIELDS, required_outputs=REQUIRED_OUTPUTS,
                    checkpointer=checkpointer, build_trace=trace,
                )
            admission = release.spec.guard
            evaluator = factories.guard_evaluator.create(admission.evaluator, policy=admission.policy, resources=resources)
            yield GuardedWorkflow(
                CompiledReleaseWorkflow(client, graph),
                RequestGuard(client=client, policy=admission.policy, evaluator=evaluator),
                image_processor=(ImageInputProcessor(release.spec.image_recognition, factories, resources)
                                 if release.spec.image_recognition is not None else None),
                history_loader=CheckpointHistoryLoader(checkpointer, client=client,
                    max_messages=admission.history_messages, max_characters=admission.history_characters),
            )

    return build
