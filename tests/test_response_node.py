import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from Models.RecipePresentationPlan import RecipePresentationPlan
from Nodes.ResponseNode import ResponseNode


def saved_recipe(recipe_id, name):
    return {
        "id": recipe_id,
        "name": name,
        "ingredients": [{"ingredient_name": "טופו", "quantity": "300", "unit": "גרם"}],
        "instructions": "1. חותכים לקוביות.\n2. מערבבים ומגישים.",
        "instagram_url": f"https://www.instagram.com/p/{recipe_id}/",
        "image_url": f"/recipe-images/{recipe_id}.jpg",
    }


class FakeLLM:
    def __init__(self, plan=None, text="בשמחה!"):
        self.plan = plan
        self.text = text
        self.structured_calls = []
        self.text_calls = []

    def with_structured_output(self, schema):
        assert schema is RecipePresentationPlan
        parent = self

        class Structured:
            async def ainvoke(self, messages):
                parent.structured_calls.append(messages)
                return parent.plan

        return Structured()

    async def ainvoke(self, messages):
        self.text_calls.append(messages)
        return AIMessage(content=self.text)


def recipe_state():
    return {
        "message": "אפשר את הטופו?",
        "messages": [HumanMessage(content="אפשר את הטופו?")],
        "result": {
            "mode": "existing",
            "recipes": [saved_recipe("tofu", "טופו חמאת בוטנים"), saved_recipe("rolls", "ספרינג רולס")],
        },
    }


def test_only_selected_recipe_has_a_photo_even_when_outro_mentions_another():
    llm = FakeLLM(RecipePresentationPlan(
        intro="הנה המתכון:", recipe_ids=["tofu"], outro="אם תרצי, אפשר גם ספרינג רולס."
    ))
    result = asyncio.run(ResponseNode(llm)(recipe_state()))

    cards = [block["recipe"] for block in result["response_blocks"] if block["type"] == "recipe"]
    assert [card["id"] for card in cards] == ["tofu"]
    assert cards[0]["image_url"] == "/recipe-images/tofu.jpg"
    assert cards[0]["instructions"] == saved_recipe("tofu", "טופו")["instructions"]
    assert "/recipe-images/rolls.jpg" not in str(result)
    assert "אם תרצי, אפשר גם ספרינג רולס." in result["final_response"]
    assert result["messages"][0].content == result["final_response"]
    assert len(llm.structured_calls) == 1
    assert not llm.text_calls


def test_selected_recipe_order_is_explicit_and_not_search_order():
    llm = FakeLLM(RecipePresentationPlan(intro="", recipe_ids=["rolls", "tofu"], outro=""))
    result = asyncio.run(ResponseNode(llm)(recipe_state()))
    cards = [block["recipe"] for block in result["response_blocks"] if block["type"] == "recipe"]
    assert [card["id"] for card in cards] == ["rolls", "tofu"]
    assert [card["image_url"] for card in cards] == ["/recipe-images/rolls.jpg", "/recipe-images/tofu.jpg"]


def test_short_answer_does_not_display_any_candidate_photos():
    llm = FakeLLM(RecipePresentationPlan(intro="בטופו יש 300 גרם.", recipe_ids=[], outro=""))
    result = asyncio.run(ResponseNode(llm)(recipe_state()))
    assert result["response_blocks"] == [{"type": "text", "text": "בטופו יש 300 גרם."}]
    assert "recipe-images" not in result["final_response"]


def test_unknown_selection_is_rejected_instead_of_attaching_another_photo():
    llm = FakeLLM(RecipePresentationPlan(intro="", recipe_ids=["not-a-candidate"], outro=""))
    with pytest.raises(ValueError):
        asyncio.run(ResponseNode(llm)(recipe_state()))


def test_text_response_replaces_previous_recipe_blocks():
    state = recipe_state()
    state["result"] = {"mode": "chat", "text": "בשמחה!"}
    state["response_blocks"] = [{"type": "recipe", "recipe": saved_recipe("old", "ישן")}]
    llm = FakeLLM(text="בשמחה!")
    result = asyncio.run(ResponseNode(llm)(state))
    assert result["response_blocks"] == [{"type": "text", "text": "בשמחה!"}]
    assert not llm.structured_calls

