from backend.tenancy import TenantContext, load_tenant

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe_plan import RecipePlan
from backend.services.contracts import BaseMessageAnalyzer


class MessageAnalyzer(BaseMessageAnalyzer):

    def __init__(self, llm: BaseChatModel, *, tenant: TenantContext | None = None):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.llm = llm

    async def analyze(
        self,
        message: str,
        current_user_message: str | None = None,
    ) -> MessageAnalysis:
        analysis = await self.llm.with_structured_output(MessageAnalysis).ainvoke(
            [
                SystemMessage(content=self.tenant.render("analyze")),
                HumanMessage(content=message),
            ]
        )
        # Heuristics must use ONLY the latest user turn — history often mentions the dietitian
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
        aliases = tuple(alias.lower() for alias in self.tenant.profile.aliases)

        create_signals = (
            "יצירתי",
            "מקורי",
            "חדש שלך",
            "רעיון שלך",
            "שלך ולא",
            "לא משהו מהמתכונים",
            "לא מהמתכונים",
            "ולא משהו",
            "original",
            "your own",
            "invent",
        )
        create_signals += tuple(
            pattern.format(alias) for alias in aliases
            for pattern in ("לא של {}", "לא מתכון של {}", "בלי {}", "not from {}", "not {}", "don't use {}")
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
        # Positive DB lookup; negations take priority below.
        lookup_signals = (
            "שמור אצלך",
            "המתכון ששמור",
            "מהמאגר",
            "מהדאטאבייס",
            "from the db",
            "from the database",
            "instagram",
            "אינסטגרם",
        )

        lookup_signals += tuple(
            pattern.format(alias) for alias in aliases
            for pattern in ("מתכון של {}", "המתכון של {}", "מתכונים של {}", "{}'s recipe", "recipe from {}")
        )
        negated_dietitian = any(
            f"של {alias}" in text and any(word in text for word in ("לא ", "ולא ", "בלי "))
            for alias in aliases
        )

        if any(s in text for s in create_signals) or negated_dietitian:
            return analysis.model_copy(update={"recipe_mode": "original"})

        if any(s in text for s in another_idea_signals):
            return analysis.model_copy(update={"recipe_mode": "original"})

        if any(s in text for s in lookup_signals) and not negated_dietitian:
            if analysis.recipe_mode in {"inspired", "original", "none"}:
                return analysis.model_copy(update={"recipe_mode": "existing"})

        return analysis

    def extract_recipe_plan(self, message: str) -> RecipePlan:
        return self.llm.with_structured_output(RecipePlan).invoke(message)

