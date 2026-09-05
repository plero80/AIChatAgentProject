from langchain_core.language_models.chat_models import BaseChatModel

from Models.RecipeCreate import RecipeCreate


class RecipeExtractorService:

    def __init__(
        self,
        llm: BaseChatModel,
    ):
        self.llm = llm.with_structured_output(
            RecipeCreate,
            method="function_calling",
        )

    def extract(
        self,
        recipe_text: str,
        instagram_url: str | None = None,
    ) -> RecipeCreate:

        recipe = self.llm.invoke([
            (
                "system",
                """
                Extract a recipe from the provided text.

                Return:
                - recipe name
                - description
                - instructions
                - ingredients
                - quantity for each ingredient when available
                - unit for each ingredient when available

                Do not invent quantities or ingredients that are
                not present in the source.

                If quantity or unit is unknown, return null.
                """
            ),
            (
                "user",
                recipe_text
            )
        ])

        recipe.instagram_url = instagram_url

        return recipe