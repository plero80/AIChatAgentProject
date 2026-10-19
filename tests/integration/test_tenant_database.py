from uuid import uuid4

import pytest
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege, UniqueViolation

from backend.models.recipe_create import IngredientCreate, RecipeCreate
from backend.persistence.repositories.recipe_repository import RecipeRepository
from backend.persistence.repositories.recipe_ingredient_repository import RecipeIngredientRepository
from backend.persistence.repositories.recipe_embedding_repository import RecipeEmbeddingRepository
from backend.persistence.schema import BASE_STATEMENTS, apply_schema
from backend.services.ingestion.recipe_ingestion_service import RecipeIngestionService
from backend.services.exceptions import RecipeAlreadyExists
from backend.tenancy import load_tenant


def register(db, identity):
    db.execute("INSERT INTO dietitians(id, display_name) VALUES (%s, %s)", (identity, "Same display name"))


def repositories(db, identity):
    return RecipeRepository(db, identity), RecipeIngredientRepository(db, identity), RecipeEmbeddingRepository(db, identity)


def recipe(name="Shared recipe", ingredient="tofu", url="https://instagram.com/p/shared/"):
    return RecipeCreate(name=name, description="Test", instructions="Cook it", instagram_url=url,
                        ingredients=[IngredientCreate(ingredient_name=ingredient, quantity=1, unit="cup")])


def save(repos, identity, value=None, vector=None):
    service = RecipeIngestionService(*repos, tenant=load_tenant(identity))
    return service.save(value or recipe(), vector or [1.0] + [0.0] * 1535, "text-embedding-3-small")


def test_all_reads_writes_and_similarity_fallback_are_scoped_even_as_superuser(isolated_database):
    db = isolated_database()
    apply_schema(db)
    for identity in ("clinic_001", "clinic_002"):
        register(db, identity)
    a = repositories(db, "clinic_001")
    b = repositories(db, "clinic_002")
    # Same recipe name and Instagram URL are allowed for different dietitians.
    first = save(a, "clinic_001")
    second = save(b, "clinic_002")
    for own, other, repos in ((first, second, a), (second, first, b)):
        recipes, ingredients, embeddings = repos
        assert recipes.get_by_id(own).id == own
        assert recipes.get_by_id(other) is None
        assert [r.id for r in recipes.get_many([other, own])] == [own]
        assert [r.id for r in recipes.search_by_name("Shared")] == [own]
        assert recipes.find_existing_id("Shared recipe") == own
        assert recipes.find_existing_id("ignored", "https://instagram.com/p/shared/") == own
        assert ingredients.find_recipe_with_ingredients(["tofu"]) == [own]
        assert ingredients.get_ingredients(other) == []
        assert len(ingredients.get_ingredients(own)) == 1
        assert embeddings.get_embeddings(other) == []
        assert len(embeddings.get_embeddings(own)) == 1
        # Empty/no ingredient candidates must still stay inside this tenant.
        for candidates in (None, [], [own, other]):
            found = embeddings.find_most_similar_recipes([1.0] + [0.0] * 1535, candidates, top_k=10)
            assert [identifier for identifier, _ in found] == [own]
        assert embeddings.find_most_similar_recipes([1.0] + [0.0] * 1535, [other]) == []
    with pytest.raises(RecipeAlreadyExists):
        save(a, "clinic_001")
    # The database itself also rejects a duplicate within one tenant.
    with pytest.raises(UniqueViolation):
        a[0].create(uuid4(), recipe())


def test_composite_foreign_keys_reject_cross_tenant_children_and_ingestion_is_atomic(isolated_database):
    db = isolated_database()
    apply_schema(db)
    register(db, "clinic_001")
    register(db, "clinic_002")
    a, b = repositories(db, "clinic_001"), repositories(db, "clinic_002")
    first = save(a, "clinic_001")
    with pytest.raises(ForeignKeyViolation):
        b[1].create_many(first, recipe().ingredients)
    with pytest.raises(ForeignKeyViolation):
        b[2].create(first, [1.0] + [0.0] * 1535, "model")
    with pytest.raises(ForeignKeyViolation):
        # Deliberately mismatched repositories: parent and ingredient must roll back.
        save((a[0], b[1], a[2]), "clinic_001", recipe(name="Should roll back", url=None))
    assert a[0].find_existing_id("Should roll back") is None


def test_rls_denies_unfiltered_reads_and_cross_tenant_writes_and_resets_connections(isolated_database):
    admin = isolated_database()
    apply_schema(admin)
    for identity in ("clinic_001", "clinic_002"):
        register(admin, identity)
        save(repositories(admin, identity), identity)
    # An ordinary runtime role is subject to policies even without WHERE filters.
    role = "runtime_" + uuid4().hex
    admin.execute(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS")
    admin.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    admin.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
    app_db = isolated_database("clinic_001", user=role)
    # Runtime startup can check an already migrated schema without DDL rights.
    apply_schema(app_db)
    assert {r["dietitian_id"] for r in app_db.fetch_all("SELECT * FROM recipes")} == {"clinic_001"}
    for table in ("recipe_ingredients", "recipe_embeddings"):
        assert {r["dietitian_id"] for r in app_db.fetch_all(f"SELECT * FROM {table}")} == {"clinic_001"}
    with pytest.raises(InsufficientPrivilege):
        RecipeRepository(app_db, "clinic_002").create(uuid4(), recipe(name="Denied"))
    assert app_db.fetch_one("SELECT count(*) AS n FROM recipes")["n"] == 1
    # SET LOCAL must not leak to the next borrower of the pooled connection.
    with app_db.pool.connection() as connection:
        assert connection.execute("SELECT current_setting('app.dietitian_id', true) AS id").fetchone()["id"] in (None, "")
        assert connection.execute("SELECT count(*) AS n FROM recipes").fetchone()["n"] == 0
    no_identity = isolated_database(user=role)
    assert no_identity.fetch_all("SELECT * FROM recipes") == []


def test_legacy_migration_requires_explicit_owner_and_is_idempotent(isolated_database):
    db = isolated_database()
    # Reproduce the pre-tenant schema, including global unique indexes.
    for statement in BASE_STATEMENTS:
        if "CREATE TABLE IF NOT EXISTS dietitians" not in statement:
            db.execute(statement)
    db.execute("CREATE UNIQUE INDEX recipes_name_key ON recipes(name)")
    db.execute("CREATE UNIQUE INDEX recipes_instagram_url_key ON recipes(instagram_url) WHERE instagram_url IS NOT NULL")
    identifier = uuid4()
    db.execute("INSERT INTO recipes(id,name) VALUES (%s, %s)", (identifier, "Legacy Alona recipe"))
    db.execute("INSERT INTO recipe_ingredients(id,recipe_id,ingredient_name) VALUES (%s,%s,%s)", (uuid4(), identifier, "tofu"))
    db.execute("INSERT INTO recipe_embeddings(recipe_id,embedding,model) VALUES (%s,%s::vector,%s)", (identifier, "[" + ",".join(["1"] + ["0"] * 1535) + "]", "model"))
    with pytest.raises(RuntimeError, match="Unowned legacy"):
        apply_schema(db)
    assert db.fetch_one("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_name='recipes' AND column_name='dietitian_id'")["n"] == 0
    apply_schema(db, legacy_profile=load_tenant("clinic_001").profile)
    apply_schema(db, legacy_profile=load_tenant("clinic_002").profile)
    for table in ("recipes", "recipe_ingredients", "recipe_embeddings"):
        assert {row["dietitian_id"] for row in db.fetch_all(f"SELECT dietitian_id FROM {table}")} == {"clinic_001"}
    register(db, "clinic_002")
    RecipeRepository(db, "clinic_002").create(uuid4(), recipe(name="Legacy Alona recipe", url=None))
    assert RecipeRepository(db, "clinic_002").get_by_id(identifier) is None


def test_two_deployments_can_initialize_one_empty_database_concurrently(isolated_database):
    from concurrent.futures import ThreadPoolExecutor

    first, second = isolated_database("clinic_001"), isolated_database("clinic_002")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(apply_schema, db) for db in (first, second)]
        for result in results:
            result.result(timeout=10)
    assert first.fetch_one("SELECT count(*) AS n FROM recipe_schema_versions")["n"] == 1
