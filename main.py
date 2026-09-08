import os

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# Config
from Config import settings

# Database
from Database.Database import Database

# Repositories
from Repositories.RecipeRepository import RecipeRepository
from Repositories.RecipeIngredientRepository import RecipeIngredientRepository
from Repositories.RecipeEmbeddingRepository import RecipeEmbeddingRepository

# Services
from Services.MessageAnalyzer import MessageAnalyzer
from Services.RecipeSearchService import RecipeSearchService
from Services.RecipeGenerationService import RecipeGenerationService

# Nodes
from Nodes.AnalyzeNode import AnalyzeNode
from Nodes.RecipeNode import RecipeNode
from Nodes.ChatNode import ChatNode
from Nodes.ResponseNode import ResponseNode

# Graph
from Graph.RecipeChatGraph import RecipeChatGraph


# Ingestion
from Services.RecipeExtractorService import RecipeExtractorService
from Services.RecipeIngestionService import RecipeIngestionService
from Graph.GraphState import RecipeIngestionState
from Nodes.Ingestion.ExtractRecipeNode import ExtractRecipeNode
from Nodes.Ingestion.PrepareEmbeddingNode import PrepareEmbeddingNode
from Nodes.Ingestion.EmbeddingNode import EmbeddingNode
from Nodes.Ingestion.PersistRecipeNode import PersistRecipeNode
from Graph.RecipeIngestionGraph import RecipeIngestionGraph

# Dietitian
from Services.DietitianService import DietitianService
from Nodes.DietitianNode import DietitianNode


load_dotenv()


def get_db_conninfo() -> str:
    """Connection string for components that manage their own pool."""
    return settings.db_conninfo()


def get_required_services():

    # --------------------------------
    # 1. Models
    # --------------------------------

    llm = ChatOpenAI(
        model=settings.CHAT_MODEL,
        max_tokens=settings.CHAT_MAX_TOKENS,
        reasoning_effort="none",
    )

    embedding_model = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
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
    )

    # --------------------------------
    # 3. Repositories
    # --------------------------------

    recipe_repository = RecipeRepository(
        db=db
    )

    ingredient_repository = RecipeIngredientRepository(
        db=db
    )

    embedding_repository = RecipeEmbeddingRepository(
        db=db
    )

    return llm, embedding_model, recipe_repository, ingredient_repository, embedding_repository





def create_app(checkpointer=None) -> RecipeChatGraph:

    llm, embedding_model, recipe_repository, ingredient_repository, embedding_repository = get_required_services()

    # --------------------------------
    # 1. Services
    # --------------------------------

    message_analyzer = MessageAnalyzer(
        llm=llm
    )

    recipe_search_service = RecipeSearchService(
        recipe_repository=recipe_repository,
        ingredient_repository=ingredient_repository,
        embedding_repository=embedding_repository,
        embedding_model=embedding_model,
    )

    recipe_generation_service = RecipeGenerationService(
        llm=llm
    )

    # --------------------------------
    # 2. Nodes
    # --------------------------------

    analyze_node = AnalyzeNode(
        analyzer=message_analyzer
    )

    recipe_node = RecipeNode(
        recipe_generation_service=recipe_generation_service,
        recipe_search_service=recipe_search_service,
    )

    chat_node = ChatNode(
        llm=llm
    )

    response_node = ResponseNode(
        llm=llm
    )

    dietitian_node = DietitianNode(
        dietitian_service=DietitianService()
    )

    # --------------------------------
    # 3. Graph
    # --------------------------------

    graph = RecipeChatGraph(
        analyze_node=analyze_node,
        recipe_node=recipe_node,
        chat_node=chat_node,
        dietitian_node=dietitian_node,
        response_node=response_node,
        checkpointer=checkpointer,
    )

    return graph


def create_ingest_recipe():

    llm, embedding_model, recipe_repository, ingredient_repository, embedding_repository = get_required_services()


    # --------------------------------
    # 1. Services
    # --------------------------------

    recipe_extractor = RecipeExtractorService(
        llm=llm
    )

    ingestion_service = RecipeIngestionService(
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
        embedding_model_name="text-embedding-3-small",
    )


    return RecipeIngestionGraph(
        extract_node=extract_node,
        prepare_embedding_node=prepare_embedding_node,
        embedding_node=embedding_node,
        persist_node=persist_node,
    )


if __name__ == "__main__":

    flag = True


    if flag:
        app = create_app()

        user_id = "test-user-1"

        while True:

            message = input("You: ")

            if message.lower() in {"exit", "quit"}:
                break

            result = app.run(
                message=message,
                user_id=user_id,
            )

            print(
                "Alona:",
                result["final_response"]
            )
        
    else:
        path = os.path.join(os.path.dirname(__file__), "Recipes", "recipe2.txt")
        try:
            with open(path, "r", encoding="utf-8") as file:
                raw_recipe = file.read()
        except FileNotFoundError:
            print(f"Recipe file not found: {path}")
            raise SystemExit(1)

        ingestion_graph = create_ingest_recipe()
        result = ingestion_graph.run(raw_recipe)
        print(result)