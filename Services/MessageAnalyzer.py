from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from Models.MessageAnalysis import MessageAnalysis
from Models.RecipePlan import RecipePlan


ANALYZE_SYSTEM = """
You classify user messages for AlonaAI, an assistant with a real recipe database of Alona Eckrling's recipes.

intent:
- food: user wants a recipe, ingredients, cooking ideas, or anything about food recipes
- dietitian_escort: nutrition / dietitian escort topics
- chat: greetings, meta questions, anything else

recipe_mode (only meaningful when intent=food):
- existing: user wants Alona's real / saved / Instagram recipe from the database.
  Use this when they say things like "מתכון של אלונה", "תביא מתכון", "יש מתכון עם…",
  "Alona's recipe", "from the DB", or ask for a recipe that already exists.
- inspired: user explicitly wants a NEW recipe inspired by Alona (not the stored one)
- original: user wants a fully original invented recipe
- none: food-related but not asking for a recipe

Default for recipe requests is existing, NOT inspired or original.
If unsure between existing and inspired → choose existing.

ingredients: short Hebrew/English ingredient keywords the user mentioned (e.g. אורז, פטריות), not full phrases.
preferences: cuisine, oven, holiday, dietary constraints, etc.
confidence: 0-1
"""


class MessageAnalyzer:

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    def analyze(self, message: str) -> MessageAnalysis:
        analysis = self.llm.with_structured_output(MessageAnalysis).invoke(
            [
                SystemMessage(content=ANALYZE_SYSTEM),
                HumanMessage(content=message),
            ]
        )
        return self._prefer_existing_for_alona_lookup(message, analysis)

    def _prefer_existing_for_alona_lookup(
        self,
        message: str,
        analysis: MessageAnalysis,
    ) -> MessageAnalysis:
        """Hard preference: explicit Alona/saved-recipe asks must look up the DB."""
        text = message.lower()
        lookup_signals = (
            "של אלונה",
            "מתכון של",
            "מתכונים של",
            "alona",
            "שמור",
            "מהמאגר",
            "מהדאטא",
            "from the db",
            "database",
            "instagram",
            "אינסטגרם",
        )
        if analysis.intent == "food" and any(s in text for s in lookup_signals):
            if analysis.recipe_mode in {"inspired", "original", "none"}:
                return analysis.model_copy(update={"recipe_mode": "existing"})
        return analysis

    def extract_recipe_plan(self, message: str) -> RecipePlan:
        return self.llm.with_structured_output(RecipePlan).invoke(message)
