"""Routing rules that kept regressing: Alona lookup vs. invent-something-new."""

import pytest

from Models.MessageAnalysis import MessageAnalysis
from Services.MessageAnalyzer import MessageAnalyzer


@pytest.fixture
def analyzer():
    # _apply_mode_overrides is pure, so no LLM is needed
    return MessageAnalyzer.__new__(MessageAnalyzer)


def analysis(intent="food", recipe_mode="existing"):
    return MessageAnalysis(
        intent=intent,
        recipe_mode=recipe_mode,
        ingredients=["אורז"],
        preferences=[],
        confidence=0.9,
    )


@pytest.mark.parametrize(
    "message",
    [
        "תביא לי מתכון של אלונה עם אורז",
        "תביא לי את המתכון ששמור אצלך",
        "יש לינק לאינסטגרם של המתכון?",
    ],
)
def test_lookup_requests_use_the_database(analyzer, message):
    result = analyzer._apply_mode_overrides(message, analysis(recipe_mode="inspired"))
    assert result.recipe_mode == "existing"


@pytest.mark.parametrize(
    "message",
    [
        "תביא לי רעיון יצירתי חדש שלך ולא משהו מהמתכונים של אלונה !!!",
        "תן לי מתכון מקורי",
        "give me something original, not from Alona",
    ],
)
def test_creative_requests_never_return_stored_recipes(analyzer, message):
    result = analyzer._apply_mode_overrides(message, analysis(recipe_mode="existing"))
    assert result.recipe_mode == "original"


@pytest.mark.parametrize(
    "message",
    ["תציע עוד רעיון", "תן לי רעיון נוסף אחר", "another idea please"],
)
def test_asking_for_another_idea_does_not_repeat_the_same_recipe(analyzer, message):
    result = analyzer._apply_mode_overrides(message, analysis(recipe_mode="existing"))
    assert result.recipe_mode == "original"


def test_negated_alona_mention_is_not_a_lookup(analyzer):
    # "של אלונה" appears, but the user is excluding it
    message = "תביא לי משהו חדש ולא מהמתכונים של אלונה"
    result = analyzer._apply_mode_overrides(message, analysis(recipe_mode="existing"))
    assert result.recipe_mode == "original"


def test_non_food_intent_is_left_alone(analyzer):
    result = analyzer._apply_mode_overrides(
        "רעיון יצירתי",
        analysis(intent="chat", recipe_mode="none"),
    )
    assert result.intent == "chat"
    assert result.recipe_mode == "none"
