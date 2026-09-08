import re



from langchain_core.language_models.chat_models import BaseChatModel



from Models.RecipeCreate import RecipeCreate



_URL_LINE = re.compile(

    r"(?im)^\s*(?:instagram_url|url)\s*=\s*(\S+)",

)





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



        recipe = self.llm.invoke(

            [

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

                - instagram_url if the text contains an Instagram URL

                  (e.g. a line like "instagram_url = https://..." or "url = ...")



                Do not invent quantities or ingredients that are

                not present in the source.



                If quantity, unit, or instagram_url is unknown, return null.

                """,

                ),

                (

                    "user",

                    recipe_text,

                ),

            ]

        )



        # Explicit arg wins; else keep LLM value; else parse from raw text

        if instagram_url:

            recipe.instagram_url = instagram_url

        elif not recipe.instagram_url:

            recipe.instagram_url = self._parse_url_from_text(recipe_text)



        return recipe



    def _parse_url_from_text(self, recipe_text: str) -> str | None:

        match = _URL_LINE.search(recipe_text)

        if not match:

            return None

        return match.group(1).rstrip(".,)")


