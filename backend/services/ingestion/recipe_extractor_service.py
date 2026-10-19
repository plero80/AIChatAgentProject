from backend.tenancy import TenantContext, load_tenant

import re


from langchain_core.language_models.chat_models import BaseChatModel


from backend.models.recipe_create import RecipeCreate
from backend.services.contracts import BaseRecipeExtractorService


_URL_LINE = re.compile(

    r"(?im)^\s*(?:instagram_url|url)\s*=\s*(\S+)",

)


class RecipeExtractorService(BaseRecipeExtractorService):


    def __init__(

        self,

        llm: BaseChatModel,
        *, tenant: TenantContext | None = None,

    ):

        self.tenant = tenant if tenant is not None else load_tenant()
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

                    self.tenant.render("extract_recipe"),

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


