"""Render explicitly selected stored recipes without inferring from prose."""

import html
import re
from collections.abc import Mapping
from urllib.parse import quote

from pydantic import BaseModel

from Models.ChatResponse import ChatResponse, PresentedRecipe, RecipeBlock, TextBlock


def build_text_response(text: str) -> ChatResponse:
    return ChatResponse(
        reply=text,
        blocks=[TextBlock(text=text)] if text else [],
    )


def build_recipe_response(
    workflow_result: dict,
    recipe_ids: list[str],
    intro: str = "",
    outro: str = "",
) -> ChatResponse:
    """Select by ID, keeping database content and media in the same block.

    Unknown IDs are rejected before rendering anything. A missing image never
    causes another candidate's photo to be used. Empty selections produce text
    only, even if the workflow returned recipe candidates.
    """
    selected_ids = list(dict.fromkeys(str(recipe_id) for recipe_id in recipe_ids))
    candidates: dict[str, dict] = {}
    for candidate in workflow_result.get("recipes") or []:
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode="json")
        if isinstance(candidate, Mapping) and candidate.get("id") is not None:
            candidates.setdefault(str(candidate["id"]), dict(candidate))

    unknown_ids = [recipe_id for recipe_id in selected_ids if recipe_id not in candidates]
    if unknown_ids:
        raise ValueError(f"Unknown selected recipe IDs: {', '.join(unknown_ids)}")

    blocks: list[TextBlock | RecipeBlock] = []
    markdown_parts: list[str] = []
    if intro:
        blocks.append(TextBlock(text=intro))
        markdown_parts.append(intro)

    for recipe_id in selected_ids:
        payload = {**candidates[recipe_id], "id": recipe_id}
        recipe = PresentedRecipe.model_validate(payload)
        blocks.append(RecipeBlock(recipe=recipe))
        markdown_parts.append(_recipe_markdown(recipe))

    if outro:
        blocks.append(TextBlock(text=outro))
        markdown_parts.append(outro)

    return ChatResponse(reply="\n\n".join(markdown_parts), blocks=blocks)


def _inline_text(value: str) -> str:
    """Keep saved names from becoming Markdown headings, links, or images."""
    text = html.escape(" ".join(value.splitlines()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+\-!|>~])", r"\\\1", text)


def _link_target(value: str) -> str:
    # Encode whitespace and delimiter characters without altering URL semantics.
    return quote(value, safe=":/?#@!$&'*+,;=%")


def _recipe_markdown(recipe: PresentedRecipe) -> str:
    name = _inline_text(recipe.name)
    sections = [f"## {name}"]
    if recipe.image_url:
        sections.append(f"![{name}](<{_link_target(recipe.image_url)}>)")
    if recipe.description:
        sections.append(recipe.description)

    if recipe.ingredients:
        ingredients = []
        for ingredient in recipe.ingredients:
            details = []
            if ingredient.quantity is not None:
                details.append(_inline_text(str(ingredient.quantity)))
            if ingredient.unit:
                details.append(_inline_text(ingredient.unit))
            line = f"- {_inline_text(ingredient.ingredient_name)}"
            if details:
                line += " — " + " ".join(details)
            ingredients.append(line)
        sections.append("### מצרכים\n\n" + "\n".join(ingredients))

    sections.append("### הוראות הכנה\n\n" + recipe.instructions)
    if recipe.instagram_url:
        sections.append(f"[למתכון באינסטגרם](<{_link_target(recipe.instagram_url)}>)")
    return "\n\n".join(sections)
