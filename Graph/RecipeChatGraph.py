from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import HumanMessage

from Graph.GraphState import GraphState
from Nodes.AnalyzeNode import AnalyzeNode
from Nodes.RecipeNode import RecipeNode
from Nodes.ResponseNode import ResponseNode
from Nodes.ChatNode import ChatNode
#from Nodes.DietitianNode import DietitianNode


class RecipeChatGraph:

    def __init__(
        self,
        analyze_node: AnalyzeNode,
        recipe_node: RecipeNode,
        response_node: ResponseNode,
        chat_node: ChatNode,
        #dietitian_node: DietitianNode,
    ):
        self.memory = InMemorySaver()

        self.nodes = {
            "analyze": analyze_node,
            "recipe": recipe_node,
            "response": response_node,
            "chat": chat_node,
            #"dietitian": dietitian_node,
        }

        self.graph = self.build_graph()


    
    def build_graph(self):

        graph = StateGraph(GraphState)

        graph.add_node(
            "analyze",
            self.nodes["analyze"],
        )

        graph.add_node(
            "chat",
            self.nodes["chat"],
        )

        graph.add_node(
            "recipe",
            self.nodes["recipe"],
        )


        #graph.add_node(
        #    "dietitian",
        #    self.nodes["dietitian"],
        #)

        graph.add_node(
            "response",
            self.nodes["response"],
        )

        # START
        graph.add_edge(
            START,
            "analyze",
        )

        # Routing
        graph.add_conditional_edges(
            "analyze",
            self.route_after_analysis,
            {
                "chat": "chat",
                "recipe": "recipe",
                #"dietitian": "dietitian",
            },
        )

        # All flows eventually go to ResponseNode
        graph.add_edge(
            "chat",
            "response",
        )

        graph.add_edge(
            "recipe",
            "response",
        )

       # graph.add_edge(
        #    "dietitian",
        #    "response",
        #)

        graph.add_edge(
            "response",
            END,
        )

        # VERY IMPORTANT
        return graph.compile(
            checkpointer=self.memory
        )

    def run(
        self,
        message: str,
        user_id: str,
    ):

        initial_state = {
            "user_id": user_id,
            "message": message,
            "messages": [
                HumanMessage(content=message)
            ],
        }

        config = {
            "configurable": {
                "thread_id": user_id
            }
        }

        result = self.graph.invoke(
            initial_state,
            config=config,
        )

        return result

    def route_after_analysis(self,state: GraphState) -> str:
        analysis = state["analysis"]
        if analysis is None:
            return "chat"

        if analysis.intent == "chat":
            return "chat"

        if analysis.intent == "food":
            return "recipe"

        #if analysis.intent == "dietitian_escort":
            #return "dietitian"

        return "chat"
