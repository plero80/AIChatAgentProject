"""Assemble configured services, nodes, and graphs for CLI and HTTP entry points."""

from collections.abc import Sequence
from contextlib import AsyncExitStack, asynccontextmanager
import logging

from langchain_openai import OpenAIEmbeddings
from langgraph.checkpoint.memory import InMemorySaver

# Config
from backend.config.settings import settings
from backend.clients.context import ClientContext
from backend.tenancy import TenantContext, load_tenant
from backend.persistence.schema import apply_schema

# Database
from backend.persistence.database import Database

# Repositories
from backend.persistence.repositories.recipe_repository import RecipeRepository
from backend.persistence.repositories.recipe_ingredient_repository import RecipeIngredientRepository
from backend.persistence.repositories.recipe_embedding_repository import RecipeEmbeddingRepository

# Service selection and construction
from backend.factories.service_factories import ServiceFactories
from backend.factories.model_registry import ModelRegistry
from backend.factories.provider_resources import openai_transport_options
from backend.config.models import ModelConfig, load_model_config
from backend.config.services import ServiceConfig, load_service_config
from backend.config.guard import GuardConfig, load_guard_config
from backend.config.images import ImageRecognitionConfig, load_image_config, validate_image_config
from backend.workflows.image_input import ImageInputProcessor

# Graph
from backend.graphs.builders.recipe_chat_builder import RecipeChatBuilder
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.workflows.adapters.recipe import RecipeWorkflowAdapter
from backend.workflows.contracts import ChatWorkflow
from backend.workflows.facade import WorkflowFacade
from backend.workflows.runtime import WorkflowFactory
from backend.workflows.guarded import GuardedWorkflow
from backend.guards.history import CheckpointHistoryLoader
from backend.guards.request_guard import RequestGuard


# Ingestion
from backend.nodes.ingestion.extract_recipe_node import ExtractRecipeNode
from backend.nodes.ingestion.prepare_embedding_node import PrepareEmbeddingNode
from backend.nodes.ingestion.embedding_node import EmbeddingNode
from backend.nodes.ingestion.persist_recipe_node import PersistRecipeNode
from backend.graphs.recipe_ingestion_graph import RecipeIngestionGraph


logger = logging.getLogger(__name__)


def get_db_conninfo() -> str:
    """Connection string for components that manage their own pool."""
    return settings.db_conninfo()


def get_required_services(
    config: ServiceConfig, factories: ServiceFactories, model_config: ModelConfig, tenant: TenantContext,
    *, resources: AsyncExitStack | None = None,
):

    # --------------------------------
    # 1. Models
    # --------------------------------

    factories.validate(config)
    models = ModelRegistry(model_config, factories.chat_model, resources=resources)
    embedding_model = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL, **openai_transport_options(resources),
    )

    # --------------------------------
    # 2. Database
    # --------------------------------

    db = Database(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        min_size=settings.DB_POOL_MIN,
        max_size=settings.DB_POOL_MAX,
        dietitian_id=tenant.dietitian_id,
    )
    if resources is not None:
        resources.callback(db.close)
    try:
        apply_schema(db)
        db.execute(
            "INSERT INTO dietitians (id, display_name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
            (tenant.dietitian_id, tenant.profile.display_name),
        )
    except BaseException:
        if resources is None:
            db.close()
        raise

    # --------------------------------
    # 3. Repositories
    # --------------------------------

    recipe_repository = RecipeRepository(
        db=db, dietitian_id=tenant.dietitian_id,
    )

    ingredient_repository = RecipeIngredientRepository(
        db=db, dietitian_id=tenant.dietitian_id,
    )

    embedding_repository = RecipeEmbeddingRepository(
        db=db, dietitian_id=tenant.dietitian_id,
    )

    return models, embedding_model, recipe_repository, ingredient_repository, embedding_repository


def create_app(
    checkpointer=None,
    *,
    service_config: ServiceConfig | None = None,
    service_factories: ServiceFactories | None = None,
    model_config: ModelConfig | None = None,
    tenant: TenantContext | None = None,
    resources: AsyncExitStack | None = None,
) -> BaseChatGraph:

    tenant = tenant if tenant is not None else load_tenant()
    config = service_config if service_config is not None else load_service_config()
    factories = service_factories if service_factories is not None else ServiceFactories()
    factories.validate(config)
    model_config = model_config if model_config is not None else load_model_config(service_config=config)
    ModelRegistry.validate(model_config, factories.chat_model)
    logger.info("Service implementations: %s", config.model_dump())
    logger.info("Model configuration: %s", model_config.model_dump())

    models, embedding_model, recipe_repository, ingredient_repository, embedding_repository = get_required_services(
        config, factories, model_config, tenant,
        **({"resources": resources} if resources is not None else {}),
    )

    # --------------------------------
    # 1. Services
    # --------------------------------

    message_analyzer = factories.message_analyzer.create(
        config.message_analyzer,
        resources=resources,
        llm=models.for_role("message_analyzer"),
        tenant=tenant,
    )

    recipe_search_service = factories.recipe_search.create(
        config.recipe_search,
        resources=resources,
        recipe_repository=recipe_repository,
        ingredient_repository=ingredient_repository,
        embedding_repository=embedding_repository,
        embedding_model=embedding_model,
    )

    recipe_generation_service = factories.recipe_generation.create(
        config.recipe_generation,
        resources=resources,
        llm=models.for_role("recipe_generation"),
        tenant=tenant,
    )

    presentation_service = factories.recipe_presentation.create(config.recipe_presentation, resources=resources)
    dietitian_service = factories.dietitian.create(config.dietitian, tenant=tenant, resources=resources)

    # --------------------------------
    # 2. Build the node container
    # --------------------------------

    builder = RecipeChatBuilder(tenant=tenant)
    builder.analysisNode(message_analyzer)
    builder.recipeNode(recipe_generation_service, recipe_search_service)
    builder.chatNode(models.for_role("chat"))
    builder.responseNode(models.for_role("response"), presentation_service)
    builder.dietitianNode(dietitian_service)
    nodes = builder.build()

    # --------------------------------
    # 3. Graph
    # --------------------------------

    graph = factories.chat_graph.create(
        config.chat_graph,
        resources=resources,
        nodes=nodes,
        checkpointer=checkpointer,
        model_registry=models,
        tenant=tenant,
    )

    return graph


def create_chat_workflow(
    checkpointer=None,
    *,
    service_config: ServiceConfig | None = None,
    service_factories: ServiceFactories | None = None,
    model_config: ModelConfig | None = None,
    tenant: TenantContext | None = None,
    graph_factory=None,
    resources: AsyncExitStack | None = None,
) -> ChatWorkflow:
    """Build the internal business adapter; managed startup adds admission checks."""
    tenant = tenant if tenant is not None else load_tenant()
    graph = (graph_factory or create_app)(
        checkpointer=checkpointer,
        service_config=service_config,
        service_factories=service_factories,
        model_config=model_config,
        tenant=tenant,
        **({"resources": resources} if resources is not None else {}),
    )
    return RecipeWorkflowAdapter(
        graph,
        client=client_for_tenant(tenant),
    )


def client_for_tenant(tenant: TenantContext) -> ClientContext:
    """The deployment snapshot is still the legacy release, resolved server-side."""
    return ClientContext(
        client_id=tenant.dietitian_id,
        display_name=tenant.profile.branding.brand_name,
    )


def create_workflow_factory(
    checkpointer,
    *,
    tenant: TenantContext | None = None,
    service_config: ServiceConfig | None = None,
    service_factories: ServiceFactories | None = None,
    model_config: ModelConfig | None = None,
    guard_config: GuardConfig | None = None,
    image_config: ImageRecognitionConfig | None = None,
    graph_factory=None,
) -> WorkflowFactory:
    """Resolve one deployment, then build owned runtimes against shared persistence.

    A custom graph_factory also receives resources= and must register cleanup
    for connections it creates. The supplied checkpointer remains caller-owned.
    """
    if checkpointer is None:
        raise ValueError("A shared checkpointer is required outside the runtime cache")
    tenant = tenant if tenant is not None else load_tenant()
    client = client_for_tenant(tenant)
    config = (service_config if service_config is not None else load_service_config()).model_copy(deep=True)
    factories = service_factories if service_factories is not None else ServiceFactories()
    factories.validate(config)
    models = (model_config if model_config is not None else load_model_config(service_config=config)).model_copy(deep=True)
    ModelRegistry.validate(models, factories.chat_model)
    admission = (guard_config if guard_config is not None else load_guard_config(tenant)).model_copy(deep=True)
    factories.guard_evaluator.validate(admission.evaluator)
    images = (image_config or load_image_config()).model_copy(deep=True)
    validate_image_config(images, factories)

    @asynccontextmanager
    async def build(resolved_client: ClientContext):
        if resolved_client != client:
            raise ValueError("Client/release does not match this configured deployment")
        async with AsyncExitStack() as resources:
            evaluator = factories.guard_evaluator.create(
                admission.evaluator, policy=admission.policy, resources=resources,
            )
            workflow = create_chat_workflow(
                checkpointer=checkpointer, tenant=tenant,
                service_config=config, service_factories=factories,
                model_config=models, graph_factory=graph_factory, resources=resources,
            )
            yield GuardedWorkflow(
                workflow,
                RequestGuard(client=client, policy=admission.policy, evaluator=evaluator),
                image_processor=ImageInputProcessor(images, factories, resources),
                history_loader=CheckpointHistoryLoader(
                    checkpointer, client=client,
                    max_messages=admission.history_messages,
                    max_characters=admission.history_characters,
                ),
            )

    return build


@asynccontextmanager
async def open_chat_workflow(
    checkpointer=None,
    *,
    tenant: TenantContext | None = None,
    service_config: ServiceConfig | None = None,
    service_factories: ServiceFactories | None = None,
    model_config: ModelConfig | None = None,
    guard_config: GuardConfig | None = None,
    image_config: ImageRecognitionConfig | None = None,
    graph_factory=None,
):
    """CLI lifetime: one facade, with history surviving any runtime rebuild."""
    tenant = tenant if tenant is not None else load_tenant()
    client = client_for_tenant(tenant)
    memory = checkpointer if checkpointer is not None else InMemorySaver()
    factory = create_workflow_factory(
        memory, tenant=tenant, service_config=service_config,
        service_factories=service_factories, model_config=model_config,
        guard_config=guard_config,
        image_config=image_config,
        graph_factory=graph_factory,
    )
    async with WorkflowFacade(factory) as facade:
        await facade.warm(client)
        yield facade.for_client(client)


def create_ingest_recipe(
    *,
    service_config: ServiceConfig | None = None,
    service_factories: ServiceFactories | None = None,
    model_config: ModelConfig | None = None,
    tenant: TenantContext | None = None,
) -> RecipeIngestionGraph:

    tenant = tenant if tenant is not None else load_tenant()
    config = service_config if service_config is not None else load_service_config()
    factories = service_factories if service_factories is not None else ServiceFactories()
    factories.validate(config)
    model_config = model_config if model_config is not None else load_model_config(service_config=config)
    ModelRegistry.validate(model_config, factories.chat_model)
    logger.info("Service implementations: %s", config.model_dump())
    logger.info("Model configuration: %s", model_config.model_dump())

    models, embedding_model, recipe_repository, ingredient_repository, embedding_repository = get_required_services(config, factories, model_config, tenant)


    # --------------------------------
    # 1. Services
    # --------------------------------

    recipe_extractor = factories.recipe_extractor.create(
        config.recipe_extractor,
        llm=models.for_role("recipe_extractor"),
        tenant=tenant,
    )

    ingestion_service = factories.recipe_ingestion.create(
        config.recipe_ingestion,
        tenant=tenant,
        recipe_repository=recipe_repository,
        ingredient_repository=ingredient_repository,
        embedding_repository=embedding_repository,
    )


    # --------------------------------
    # 2. Nodes
    # --------------------------------

    extract_node = ExtractRecipeNode(
        extractor=recipe_extractor
    )

    prepare_embedding_node = PrepareEmbeddingNode()

    embedding_node = EmbeddingNode(
        embedding_model=embedding_model
    )

    persist_node = PersistRecipeNode(
        ingestion_service=ingestion_service,
        embedding_model_name=settings.EMBEDDING_MODEL,
    )


    return RecipeIngestionGraph(
        extract_node=extract_node,
        prepare_embedding_node=prepare_embedding_node,
        embedding_node=embedding_node,
        persist_node=persist_node,
        model_registry=models,
        tenant=tenant,
    )


def create_commands():
    """Register each task's input/output handler and lazy graph factory here."""
    from backend.cli.commands.chat_command import ChatCommand
    from backend.cli.commands.ingest_command import IngestCommand

    return {
        "chat": ChatCommand(open_chat_workflow),
        "ingest": IngestCommand(create_ingest_recipe),
    }


def main(argv: Sequence[str] | None = None) -> int:
    from backend.cli.runner import run_cli

    return run_cli(argv, commands=create_commands())


if __name__ == "__main__":
    raise SystemExit(main())
