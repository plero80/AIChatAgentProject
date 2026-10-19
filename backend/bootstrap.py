"""Assemble configured services, nodes, and graphs for CLI and HTTP entry points."""

from collections.abc import Sequence
import logging

from langchain_openai import OpenAIEmbeddings

# Config
from backend.config.settings import settings
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
from backend.config.models import ModelConfig, load_model_config
from backend.config.services import ServiceConfig, load_service_config

# Graph
from backend.graphs.builders.recipe_chat_builder import RecipeChatBuilder
from backend.graphs.base_chat_graph import BaseChatGraph


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
):

    # --------------------------------
    # 1. Models
    # --------------------------------

    factories.validate(config)
    models = ModelRegistry(model_config, factories.chat_model)
    embedding_model = OpenAIEmbeddings(model=settings.EMBEDDING_MODEL)

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
    try:
        apply_schema(db)
        db.execute(
            "INSERT INTO dietitians (id, display_name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
            (tenant.dietitian_id, tenant.profile.display_name),
        )
    except BaseException:
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
) -> BaseChatGraph:

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

    message_analyzer = factories.message_analyzer.create(
        config.message_analyzer,
        llm=models.for_role("message_analyzer"),
        tenant=tenant,
    )

    request_validator = factories.request_validator.create(
        config.request_validator,
        llm=models.for_role("request_validator"),
        tenant=tenant,
    )

    recipe_search_service = factories.recipe_search.create(
        config.recipe_search,
        recipe_repository=recipe_repository,
        ingredient_repository=ingredient_repository,
        embedding_repository=embedding_repository,
        embedding_model=embedding_model,
    )

    recipe_generation_service = factories.recipe_generation.create(
        config.recipe_generation,
        llm=models.for_role("recipe_generation"),
        tenant=tenant,
    )

    presentation_service = factories.recipe_presentation.create(config.recipe_presentation)
    dietitian_service = factories.dietitian.create(config.dietitian, tenant=tenant)

    # --------------------------------
    # 2. Build the node container
    # --------------------------------

    builder = RecipeChatBuilder(tenant=tenant)
    builder.analysisNode(message_analyzer)
    builder.recipeNode(recipe_generation_service, recipe_search_service)
    builder.chatNode(models.for_role("chat"))
    builder.responseNode(models.for_role("response"), presentation_service)
    builder.dietitianNode(dietitian_service)
    builder.guardNode(request_validator)
    nodes = builder.build()

    # --------------------------------
    # 3. Graph
    # --------------------------------

    graph = factories.chat_graph.create(
        config.chat_graph,
        nodes=nodes,
        checkpointer=checkpointer,
        model_registry=models,
        tenant=tenant,
    )

    return graph


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
        "chat": ChatCommand(create_app),
        "ingest": IngestCommand(create_ingest_recipe),
    }


def main(argv: Sequence[str] | None = None) -> int:
    from backend.cli.runner import run_cli

    return run_cli(argv, commands=create_commands())


if __name__ == "__main__":
    raise SystemExit(main())
