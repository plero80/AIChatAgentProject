"""Recipe photos must follow explicit selection, never incidental mentions."""

from decimal import Decimal

import pytest

from Models.ChatResponse import ChatResponse
from Services.RecipePresentationService import build_recipe_response, build_text_response


def recipe(recipe_id, name, **overrides):
    return {
        "id": recipe_id,
        "name": name,
        "description": f"Description of {name}",
        "ingredients": [
            {"ingredient_name": "טופו", "quantity": "300", "unit": "גרם"},
            {"ingredient_name": "בצל ירוק", "quantity": None, "unit": None},
        ],
        "instructions": "1. חותכים את הטופו.\n2. מוסיפים **רוטב** ומגישים.",
        "instagram_url": f"https://www.instagram.com/p/{recipe_id}/",
        "image_url": f"https://images.example.com/{recipe_id}.jpg",
        **overrides,
    }


@pytest.fixture
def candidates():
    return {"recipes": [recipe("tofu", "טופו חמאת בוטנים"), recipe("rolls", "ספרינג רולס")]}


def test_only_selected_recipe_gets_a_card_even_when_outro_mentions_another(candidates):
    outro = "אפשר גם להכין ספרינג רולס בפעם הבאה."
    response = build_recipe_response(candidates, ["tofu"], "הנה המתכון:", outro)

    assert [block.type for block in response.blocks] == ["text", "recipe", "text"]
    assert response.blocks[1].recipe.id == "tofu"
    assert response.blocks[2].text == outro
    assert "https://images.example.com/tofu.jpg" in response.reply
    assert "https://images.example.com/rolls.jpg" not in response.reply
    assert "https://www.instagram.com/p/rolls/" not in response.reply
    assert response.reply.endswith(outro)


def test_selection_order_places_each_photo_inside_its_own_recipe(candidates):
    response = build_recipe_response(candidates, ["rolls", "tofu"], "פתיחה", "סיום")

    assert [block.recipe.id for block in response.blocks if block.type == "recipe"] == [
        "rolls", "tofu"
    ]
    first_title = response.reply.index("## ספרינג רולס")
    first_image = response.reply.index("https://images.example.com/rolls.jpg")
    first_instructions = response.reply.index("### הוראות הכנה")
    second_title = response.reply.index("## טופו חמאת בוטנים")
    second_image = response.reply.index("https://images.example.com/tofu.jpg")
    assert first_title < first_image < first_instructions < second_title < second_image
    assert response.reply.startswith("פתיחה\n\n")
    assert response.reply.endswith("\n\nסיום")


def test_card_preserves_all_display_fields_and_exact_saved_instructions(candidates):
    response = build_recipe_response(candidates, ["tofu"])
    saved = candidates["recipes"][0]

    assert response.blocks[0].recipe.model_dump() == saved
    assert saved["description"] in response.reply
    assert saved["instructions"] in response.reply
    assert saved["instagram_url"] in response.reply
    assert "טופו — 300 גרם" in response.reply
    assert "- בצל ירוק" in response.reply


def test_null_quantities_zero_and_decimal_precision_survive_without_an_image():
    ingredients = [
        {"ingredient_name": "מים", "quantity": "0.2500", "unit": "כוס"},
        {"ingredient_name": "מלח", "quantity": 0, "unit": "גרם"},
        {"ingredient_name": "פלפל", "quantity": None, "unit": None},
        {"ingredient_name": "סויה", "quantity": Decimal("1.234567890123456789"), "unit": "כף"},
    ]
    saved = recipe("tofu", "טופו", image_url=None, ingredients=ingredients)
    other = recipe("rolls", "ספרינג רולס")
    response = build_recipe_response({"recipes": [saved, other]}, ["tofu"])

    card = response.blocks[0].recipe
    assert card.image_url is None
    assert [item.quantity for item in card.ingredients] == [
        "0.2500", 0, None, "1.234567890123456789"
    ]
    assert card.instructions == saved["instructions"]
    assert "0.2500 כוס" in response.reply
    assert "מלח — 0 גרם" in response.reply
    assert "1.234567890123456789 כף" in response.reply
    assert "![" not in response.reply
    assert "rolls.jpg" not in response.reply
    # Wire serialization must keep numeric zero and string precision intact.
    assert ChatResponse.model_validate_json(response.model_dump_json()) == response


def test_unknown_recipe_ids_are_rejected(candidates):
    with pytest.raises(ValueError, match="Unknown selected recipe IDs: missing"):
        build_recipe_response(candidates, ["tofu", "missing"])


def test_duplicate_ids_do_not_repeat_recipe_cards_or_photos(candidates):
    response = build_recipe_response(candidates, ["rolls", "rolls", "tofu", "rolls"])

    assert [block.recipe.id for block in response.blocks] == ["rolls", "tofu"]
    assert response.reply.count("rolls.jpg") == 1
    assert response.reply.count("tofu.jpg") == 1


def test_empty_selection_keeps_text_and_never_attaches_candidate_images(candidates):
    intro = "  הנה רעיון אחר.\n"
    outro = "\nאפשר לשאול על טופו.  "
    response = build_recipe_response(candidates, [], intro, outro)

    assert response.reply == intro + "\n\n" + outro
    assert [block.text for block in response.blocks] == [intro, outro]
    assert all(block.type == "text" for block in response.blocks)


def test_text_response_and_backwards_compatible_default_blocks():
    text = "שלום!\n\n**מה מתחשק להכין?**"
    assert build_text_response(text).model_dump() == {
        "reply": text,
        "blocks": [{"type": "text", "text": text}],
    }
    assert build_text_response("").blocks == []
    assert ChatResponse(reply=text).blocks == []


def test_recipe_names_cannot_inject_markdown_images_or_headings():
    name = "**טופו** [קישור](https://other.example)\n# תמונה ![x](bad)"
    saved = recipe("tofu", name, description=None)
    response = build_recipe_response({"recipes": [saved]}, ["tofu"])

    assert response.blocks[0].recipe.name == name
    assert response.reply.startswith("## \\*\\*טופו\\*\\* \\[קישור\\]\\(")
    assert "\n# תמונה" not in response.reply
    assert "![x](bad)" not in response.reply
    assert response.reply.count("https://images.example.com/tofu.jpg") == 1
