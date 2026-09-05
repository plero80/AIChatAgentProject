import os

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

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


load_dotenv()


def get_required_services():

    # --------------------------------
    # 1. Models
    # --------------------------------

    llm = ChatOpenAI(
        model="gpt-5.4-mini",
        max_tokens=16000,
        reasoning_effort="none",
    )

    embedding_model = OpenAIEmbeddings(
        model="text-embedding-3-small",
    )

    # --------------------------------
    # 2. Database
    # --------------------------------

    db = Database(
        host="localhost",
        port=5432,
        dbname="recipes_db",
        user="postgres",
        password=os.getenv("POSTGRES_PASSWORD"),
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





def create_app() -> RecipeChatGraph:

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

    # --------------------------------
    # 3. Graph
    # --------------------------------

    graph = RecipeChatGraph(
        analyze_node=analyze_node,
        recipe_node=recipe_node,
        chat_node=chat_node,
        response_node=response_node,
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
        path = os.path.join(os.path.dirname(__file__), "recipes", "recipe1.txt")
        try:
            with open(path, "r", encoding="utf-8") as file:
                raw_recipe = file.read()
        except FileNotFoundError:
            print(f"Recipe file not found: {path}")
            raise SystemExit(1)

        ingestion_graph = create_ingest_recipe()
        result = ingestion_graph.run(raw_recipe, "https://www.instagram.com/p/DcyOklRCMXs/")
        print(result)