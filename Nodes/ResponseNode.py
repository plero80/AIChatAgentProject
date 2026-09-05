from langchain_core.language_models.chat_models import BaseChatModel

from Graph.GraphState import GraphState


class ResponseNode:

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    def __call__(self, state: GraphState) -> dict:
        response = self.llm.invoke(
            [
                (
                    "system",
                    """
                You are Alona's assistant.

                Create the final response to the user using ONLY the
                workflow result below. Match the user's language.

                Rules:
                - You DO have access to Alona's recipe database via the workflow.
                - NEVER say you cannot access a database, cannot look up saved
                  recipes, or that recipes are not available in storage.
                - If mode is existing: present the recipe(s) from the result
                  faithfully. ALWAYS include:
                  name, full ingredients list (name + quantity + unit when present),
                  instructions, and Instagram URL.
                  Do not invent or rewrite into a different recipe.
                - If mode is not_found: say no matching Alona recipe was found.
                  Offer to search with different ingredients or generate an
                  inspired recipe — do NOT invent one unless asked.
                - If mode is inspired: clearly say it is inspired, not an
                  official Alona recipe.
                - If mode is original: present it as an original suggestion.
                - If mode is chat: use the provided chat text.
                """,
                ),
                (
                    "user",
                    f"""
                Original user request:
                {state["message"]}

                Workflow result:
                {state["result"]}
                """,
                ),
            ]
        )

        return {
            "final_response": response.content,
            "messages": [response],
        }
