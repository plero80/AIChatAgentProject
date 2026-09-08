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
  Examples: "מתכון של אלונה", "תביא מתכון שמור", "יש מתכון עם…", "Alona's recipe".
- inspired: user wants a NEW recipe inspired by Alona's style, but NOT a stored recipe.
  Examples: "בהשראה", "בסגנון אלונה אבל חדש".
- original: user wants a fully original invented recipe from YOU, not from Alona's database.
  Examples: "רעיון יצירתי שלך", "מתכון מקורי", "לא מהמתכונים של אלונה",
  "something new of your own", "another different idea" when they reject repeating DB recipes.
- none: food-related but not asking for a recipe

Rules:
- If the user asks for YOUR creative/original idea, or explicitly does NOT want Alona's
  stored recipes → original (never existing).
- If they ask for "another idea" / "עוד רעיון" / "רעיון אחר" after already getting a DB
  recipe → prefer original or inspired, not the same existing recipe again.
- Default for first-time "what can I cook with X" without rejecting the DB → existing.
- Mentions of Alona in a NEGATIVE sense ("לא של אלונה", "ולא מהמתכונים של אלונה")
  are NOT existing requests.

ingredients: short Hebrew/English ingredient keywords from the current request and
recent conversation (e.g. אורז, תירס), not full phrases.
preferences: cuisine, crispy, oven, holiday, dietary constraints, etc.
confidence: 0-1
"""


class MessageAnalyzer:

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    async def analyze(
        self,
        message: str,
        current_user_message: str | None = None,
    ) -> MessageAnalysis:
        analysis = await self.llm.with_structured_output(MessageAnalysis).ainvoke(
            [
                SystemMessage(content=ANALYZE_SYSTEM),
                HumanMessage(content=message),
            ]
        )
        # Heuristics must use ONLY the latest user turn — history often mentions Alona
        return self._apply_mode_overrides(
            current_user_message or message,
            analysis,
        )

    def _apply_mode_overrides(
        self,
        current_message: str,
        analysis: MessageAnalysis,
    ) -> MessageAnalysis:
        if analysis.intent != "food":
            return analysis

        text = current_message.lower()

        create_signals = (
            "יצירתי",
            "מקורי",
            "חדש שלך",
            "רעיון שלך",
            "שלך ולא",
            "לא משהו מהמתכונים",
            "לא מהמתכונים",
            "ולא משהו",
            "לא של אלונה",
            "לא מתכון של אלונה",
            "בלי אלונה",
            "original",
            "your own",
            "not from alona",
            "not alona",
            "don't use alona",
            "invent",
        )
        another_idea_signals = (
            "עוד רעיון",
            "רעיון נוסף",
            "רעיון אחר",
            "משהו אחר",
            "another idea",
            "something else",
            "different idea",
            "something different",
        )
        # Positive DB lookup — avoid matching negated "של אלונה" / "מהמתכונים של אלונה"
        lookup_signals = (
            "מתכון של אלונה",
            "המתכון של אלונה",
            "מתכונים של אלונה",
            "שמור אצלך",
            "המתכון ששמור",
            "מהמאגר",
            "מהדאטאבייס",
            "from the db",
            "from the database",
            "instagram",
            "אינסטגרם",
        )

        negated_alona = any(
            phrase in text
            for phrase in (
                "לא מהמתכונים של אלונה",
                "ולא משהו מהמתכונים של אלונה",
                "לא של אלונה",
                "לא מתכון של אלונה",
                "ולא משהו מהמתכונים",
                "not from alona",
                "not alona",
            )
        ) or ("של אלונה" in text and any(n in text for n in ("לא ", "ולא ", "בלי ")))

        if any(s in text for s in create_signals) or negated_alona:
            return analysis.model_copy(update={"recipe_mode": "original"})

        if any(s in text for s in another_idea_signals):
            return analysis.model_copy(update={"recipe_mode": "original"})

        if any(s in text for s in lookup_signals) and not negated_alona:
            if analysis.recipe_mode in {"inspired", "original", "none"}:
                return analysis.model_copy(update={"recipe_mode": "existing"})

        return analysis

    def extract_recipe_plan(self, message: str) -> RecipePlan:
        return self.llm.with_structured_output(RecipePlan).invoke(message)
