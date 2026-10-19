import asyncio
import json
import shutil
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import backend.bootstrap as bootstrap
import backend.web.api as api
from backend.graphs.graph_requests import ChatGraphRequest, RecipeIngestionRequest
from backend.graphs.recipe_chat_graph import RecipeChatGraph
from backend.graphs.recipe_chat_nodes import RecipeChatNodes
from backend.graphs.recipe_ingestion_graph import RecipeIngestionGraph
from backend.models.message_analysis import MessageAnalysis
from backend.models.recipe_create import RecipeCreate
from backend.paths import CONFIG_DIR
from backend.services.chat.message_analyzer import MessageAnalyzer
from backend.services.ingestion.recipe_ingestion_service import _attach_saved_image
from backend.tenancy import PROMPT_ARGUMENTS, load_tenant


@pytest.fixture
def profiles(tmp_path):
    shutil.copytree(CONFIG_DIR / "dietitians", tmp_path / "dietitians")
    shutil.copytree(CONFIG_DIR / "prompts", tmp_path / "prompts")
    return tmp_path


def test_profiles_render_every_prompt_without_inheriting_another_dietitians_name():
    for tenant_id, own_name, other_name in (("clinic_001", "אלונה", "דנה"), ("clinic_002", "דנה", "אלונה")):
        tenant = load_tenant(tenant_id)
        for name, arguments in PROMPT_ARGUMENTS.items():
            text = tenant.render(name, **{arg: "sample" for arg in arguments})
            assert "${" not in text
            assert other_name not in text
            assert ("Alona" if tenant_id == "clinic_002" else "Dana") not in text
        assert own_name in tenant.render("chat")


def test_same_display_name_can_belong_to_two_different_ids(profiles):
    path = profiles / "dietitians/clinic_002/profile.json"
    values = json.loads(path.read_text(encoding="utf-8"))
    values["display_name"] = "Alona Eckrling"
    path.write_text(json.dumps(values), encoding="utf-8")
    first, second = (load_tenant(i, config_dir=profiles) for i in ("clinic_001", "clinic_002"))
    assert first.profile.display_name == second.profile.display_name
    assert first.dietitian_id != second.dietitian_id


def test_prompt_override_is_cached_until_a_new_startup_and_values_are_not_reformatted(profiles):
    path = profiles / "dietitians/clinic_002/prompts/chat.txt"
    path.write_text("Hello from ${assistant_name}", encoding="utf-8")
    tenant = load_tenant("clinic_002", config_dir=profiles)
    assert tenant.render("chat") == "Hello from DanaAI"
    path.write_text("Changed", encoding="utf-8")
    assert tenant.render("chat") == "Hello from DanaAI"
    assert load_tenant("clinic_002", config_dir=profiles).render("chat") == "Changed"
    assert "${display_name}" in tenant.render("generate_original", ingredients="${display_name}", preferences="{}")
    with pytest.raises(TypeError):
        tenant.prompts["chat"] = "Changed"


@pytest.mark.parametrize("content", ["", "${unknown_option}", "$broken{"])
def test_invalid_prompt_fails_startup(profiles, content):
    (profiles / "dietitians/clinic_001/prompts/chat.txt").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_tenant("clinic_001", config_dir=profiles)


@pytest.mark.parametrize("identifier", ["missing", "../clinic_001", "", "clinic_001:session"])
def test_bad_tenant_does_not_create_external_dependencies(monkeypatch, identifier):
    monkeypatch.setenv("DIETITIAN_ID", identifier)
    dependencies = MagicMock(side_effect=AssertionError("External dependency opened"))
    monkeypatch.setattr(bootstrap, "get_required_services", dependencies)
    with pytest.raises(ValueError):
        bootstrap.create_app()
    with pytest.raises(ValueError):
        bootstrap.create_ingest_recipe()
    dependencies.assert_not_called()


def test_railway_requires_explicit_selection_and_profile_id_must_match(profiles):
    with pytest.raises(ValueError, match="Set DIETITIAN_ID"):
        load_tenant(environ={"PORT": "8080"})
    path = profiles / "dietitians/clinic_001/profile.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"clinic_001"', '"clinic_002"'), encoding="utf-8")
    with pytest.raises(ValueError, match="must match"):
        load_tenant("clinic_001", config_dir=profiles)


@pytest.mark.parametrize("message, mode", [
    ("תביא מתכון של דנה", "existing"),
    ("לא של דנה", "original"),
    ("give me Dana's recipe", "existing"),
    ("don't use Dana", "original"),
    ("תביא מתכון של אלונה", "inspired"),
])
def test_alias_routing_uses_selected_profile(message, mode):
    analyzer = MessageAnalyzer(None, tenant=load_tenant("clinic_002"))
    result = analyzer._apply_mode_overrides(message, MessageAnalysis(intent="food", recipe_mode="inspired"))
    assert result.recipe_mode == mode


def test_two_graphs_with_one_checkpointer_and_same_user_keep_histories_separate():
    memory = InMemorySaver()

    async def noop(state):
        return {}

    async def respond(state):
        return {"final_response": state["message"], "messages": [AIMessage(content=state["message"])]}

    nodes = RecipeChatNodes(analysis=noop, guard=noop, response=respond, chat=noop, recipe=noop, dietitian=noop)
    first = RecipeChatGraph(nodes, checkpointer=memory, tenant=load_tenant("clinic_001"))
    second = RecipeChatGraph(nodes, checkpointer=memory, tenant=load_tenant("clinic_002"))

    async def conversation():
        a, b = await asyncio.gather(
            first.arun(ChatGraphRequest("Alona private turn", "same-cookie", "clinic_001")),
            second.arun(ChatGraphRequest("Dana private turn", "same-cookie", "clinic_002")),
        )
        assert a["dietitian_id"] == "clinic_001"
        assert b["dietitian_id"] == "clinic_002"
        followup = await second.arun(ChatGraphRequest("Again", "same-cookie", "clinic_002"))
        assert "Dana private turn" in str(followup["messages"])
        assert "Alona private turn" not in str(followup["messages"])
        with pytest.raises(ValueError, match="does not match"):
            await first.arun(ChatGraphRequest("Spoof", "same-cookie", "clinic_002"))
        ingestion = RecipeIngestionGraph(noop, noop, noop, noop, tenant=load_tenant("clinic_001"))
        with pytest.raises(ValueError, match="does not match"):
            await ingestion.arun(RecipeIngestionRequest("Spoof", "clinic_002"))

    asyncio.run(conversation())


def fake_checkpoint_pool(monkeypatch):
    pool = MagicMock()
    pool.__aenter__ = AsyncMock(return_value=pool)
    pool.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(api, "AsyncConnectionPool", MagicMock(return_value=pool))
    memory = InMemorySaver()
    memory.setup = AsyncMock()
    monkeypatch.setattr(api, "AsyncPostgresSaver", lambda _: memory)
    monkeypatch.setattr(api, "enforce_rate_limits", lambda *_: None)


def test_two_api_deployments_use_server_identity_branding_and_image_folder(monkeypatch, tmp_path):
    fake_checkpoint_pool(monkeypatch)
    requests = []

    class Graph:
        async def arun(self, request):
            requests.append(request)
            return {"final_response": request.dietitian_id}

    created = []

    def factory(**dependencies):
        created.append(dependencies["tenant"].dietitian_id)
        return Graph()

    first = load_tenant("clinic_001")
    second = replace(load_tenant("clinic_002"), images_dir=tmp_path)
    application_a = api.create_http_app(tenant=first, graph_factory=factory)
    application_b = api.create_http_app(tenant=second, graph_factory=factory)
    with TestClient(application_a) as a, TestClient(application_b) as b:
        for client, identity in ((a, "clinic_001"), (b, "clinic_002")):
            assert client.get("/public-config").json()["dietitian_id"] == identity
            assert "prompts" not in client.get("/public-config").json()
            assert client.post("/chat", json={"message": "Hi"}).json()["reply"] == identity
            assert client.post("/chat", json={"message": "Hi", "dietitian_id": "other"}).status_code == 422
            assert client.post("/chat", json={"message": "   "}).status_code == 422
        b.cookies.update(a.cookies)
        assert b.post("/chat?dietitian_id=clinic_001", json={"message": "Hello"}).json()["reply"] == "clinic_002"
        assert requests[-1].user_id == requests[0].user_id
        assert a.get("/recipe-images/Db3SxwotrQg.jpg").status_code == 200
        assert b.get("/recipe-images/Db3SxwotrQg.jpg").status_code == 404
        assert a.get("/recipe-images/..%5C..%5Cprofile.json").status_code == 404
        monkeypatch.setenv("DIETITIAN_ID", "clinic_002")
        assert a.get("/public-config").json()["dietitian_id"] == "clinic_001"
    assert created == ["clinic_001", "clinic_002"]


def test_other_tenant_does_not_attach_alonas_bundled_image():
    recipe = RecipeCreate(name="Dana recipe", description="Test", ingredients=[], instagram_url="https://www.instagram.com/p/Db3SxwotrQg/")
    _attach_saved_image(recipe, load_tenant("clinic_002"))
    assert recipe.image_url is None


def test_bootstrap_injects_the_same_snapshot_into_every_prompt_aware_component(monkeypatch):
    tenant = load_tenant("clinic_002")
    models = SimpleNamespace(for_role=lambda _: MagicMock())
    monkeypatch.setattr(bootstrap, "get_required_services", lambda *args: (models, object(), object(), object(), object()))
    graph = bootstrap.create_app(tenant=tenant)
    for component in (
        graph, graph.nodes.analysis.analyzer, graph.nodes.guard, graph.nodes.guard.validator,
        graph.nodes.chat, graph.nodes.recipe, graph.nodes.recipe.recipe_generation_service,
        graph.nodes.response, graph.nodes.dietitian, graph.nodes.dietitian.dietitian_service,
    ):
        assert component.tenant is tenant
    ingestion = bootstrap.create_ingest_recipe(tenant=tenant)
    assert ingestion.tenant is tenant
    assert ingestion.extract_node.extractor.tenant is tenant
    assert ingestion.persist_node.ingestion_service.tenant is tenant


def test_invalid_api_identity_fails_before_checkpoint_connections(monkeypatch):
    monkeypatch.setenv("DIETITIAN_ID", "unknown")
    pool = MagicMock(side_effect=AssertionError("Database opened"))
    monkeypatch.setattr(api, "AsyncConnectionPool", pool)
    with pytest.raises(ValueError, match="Unknown DIETITIAN_ID"):
        with TestClient(api.create_http_app()):
            pytest.fail("Startup should have failed")
    pool.assert_not_called()
