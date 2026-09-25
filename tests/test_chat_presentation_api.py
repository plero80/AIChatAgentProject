import api
from fastapi.testclient import TestClient

from Services.RecipePresentationService import build_recipe_response


def request_with_result(monkeypatch, result):
    class Graph:
        async def arun(self, message, user_id):
            return result

    monkeypatch.setattr(api, "get_graph", lambda: Graph())
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)
    # No lifespan context: use the fake graph without starting database pools.
    client = TestClient(api.app)
    try:
        response = client.post("/chat", json={"message": "אפשר את הטופו?"})
        assert response.status_code == 200
        return response.json()
    finally:
        client.close()


def test_chat_api_returns_only_explicitly_presented_recipe_media(monkeypatch):
    recipes = [
        {
            "id": recipe_id, "name": recipe_id, "ingredients": [],
            "instructions": "Cook and serve.",
            "image_url": f"/recipe-images/{recipe_id}.jpg",
        }
        for recipe_id in ("tofu", "rolls")
    ]
    workflow = {"mode": "existing", "recipes": recipes}
    presentation = build_recipe_response(workflow, ["tofu"], "Here is the tofu.", "Ask about rolls next.")
    payload = request_with_result(monkeypatch, {
        "final_response": presentation.reply,
        "response_blocks": [block.model_dump(mode="json") for block in presentation.blocks],
        "result": workflow,
    })

    assert payload == presentation.model_dump(mode="json")
    assert "/recipe-images/tofu.jpg" in str(payload)
    assert "/recipe-images/rolls.jpg" not in str(payload)


def test_chat_api_does_not_append_candidate_photos_when_presentation_is_absent(monkeypatch):
    payload = request_with_result(monkeypatch, {
        "final_response": "איזה מתכון תרצי?",
        "result": {"mode": "existing", "recipes": [{"name": "טופו", "image_url": "/recipe-images/tofu.jpg"}]},
    })
    assert payload == {
        "reply": "איזה מתכון תרצי?",
        "blocks": [{"type": "text", "text": "איזה מתכון תרצי?"}],
    }


def test_chat_api_preserves_explicit_empty_presentation(monkeypatch):
    payload = request_with_result(monkeypatch, {
        "final_response": "legacy", "response_blocks": [], "result": None,
    })
    assert payload["blocks"] == []
