"""Tenant recipe schema and an explicit migration for unowned legacy rows."""

import logging

from backend.tenancy import DietitianProfile

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2

BASE_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS dietitians (
        id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        CHECK (id ~ '^[a-z][a-z0-9_]{0,63}$')
    )""",
    """CREATE TABLE IF NOT EXISTS recipes (
        id UUID PRIMARY KEY, name TEXT NOT NULL, description TEXT,
        instructions TEXT, instagram_url TEXT, image_url TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS recipe_ingredients (
        id UUID PRIMARY KEY, recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        ingredient_name TEXT NOT NULL, quantity DOUBLE PRECISION, unit TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS recipe_embeddings (
        recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        embedding vector(1536) NOT NULL, model TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS image_url TEXT",
)
TENANT_TABLES = ("recipes", "recipe_ingredients", "recipe_embeddings")


def apply_schema(db, *, legacy_profile: DietitianProfile | None = None) -> None:
    """Fresh databases work automatically; existing recipes need an explicit migration.

    Run with the schema owner when migrating. The version check lets later
    application connections use the already prepared schema without DDL rights.
    Everything, including the backfill, is rolled back if any constraint fails.
    """
    with db.transaction() as tx:
        # Serialize startup/migrations from separate deployments sharing a DB.
        tx.execute("SELECT pg_advisory_xact_lock(740291830124)")
        if tx.fetch_one("SELECT to_regclass('recipe_schema_versions') AS name")["name"]:
            version = tx.fetch_one("SELECT MAX(version) AS version FROM recipe_schema_versions")["version"]
            if version == SCHEMA_VERSION:
                return
            if version and version > SCHEMA_VERSION:
                raise RuntimeError("Recipe database schema is newer than this application")

        # Never let RLS silently hide rows from an administrative migration.
        tx.execute("SET LOCAL row_security = off")
        tx.execute("CREATE TABLE IF NOT EXISTS recipe_schema_versions (version INTEGER PRIMARY KEY)")
        for statement in BASE_STATEMENTS:
            tx.execute(statement)
        for table in TENANT_TABLES:
            tx.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS dietitian_id TEXT")

        unowned = tx.fetch_one(
            "SELECT EXISTS (SELECT 1 FROM recipes WHERE dietitian_id IS NULL) AS present"
        )["present"]
        if unowned and legacy_profile is None:
            raise RuntimeError(
                "Unowned legacy recipes found. Stop the old servers, back up the database, "
                "then run: python -m backend.persistence.migrate --legacy-dietitian-id <owner-id>"
            )
        if unowned:
            tx.execute(
                "INSERT INTO dietitians (id, display_name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (legacy_profile.dietitian_id, legacy_profile.display_name),
            )
            tx.execute(
                "UPDATE recipes SET dietitian_id = %s WHERE dietitian_id IS NULL",
                (legacy_profile.dietitian_id,),
            )
        for table in TENANT_TABLES[1:]:
            tx.execute(f"""UPDATE {table} AS child SET dietitian_id = parent.dietitian_id
                FROM recipes AS parent WHERE child.recipe_id = parent.id AND child.dietitian_id IS NULL""")
        for table in TENANT_TABLES:
            tx.execute(f"ALTER TABLE {table} ALTER COLUMN dietitian_id SET NOT NULL")

        # Names and Instagram posts may repeat across dietitians.
        for name in ("recipes_name_key", "recipes_instagram_url_key"):
            tx.execute(f"ALTER TABLE recipes DROP CONSTRAINT IF EXISTS {name}")
            tx.execute(f"DROP INDEX IF EXISTS {name}")
        tx.execute("ALTER TABLE recipes ADD CONSTRAINT recipes_tenant_fkey FOREIGN KEY (dietitian_id) REFERENCES dietitians(id)")
        tx.execute("CREATE UNIQUE INDEX recipes_tenant_id_key ON recipes(dietitian_id, id)")
        tx.execute("CREATE UNIQUE INDEX recipes_tenant_name_key ON recipes(dietitian_id, name)")
        tx.execute("""CREATE UNIQUE INDEX recipes_tenant_instagram_key
            ON recipes(dietitian_id, instagram_url) WHERE instagram_url IS NOT NULL""")
        for table in TENANT_TABLES[1:]:
            tx.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_recipe_id_fkey")
            tx.execute(f"""ALTER TABLE {table} ADD CONSTRAINT {table}_tenant_recipe_fkey
                FOREIGN KEY (dietitian_id, recipe_id) REFERENCES recipes(dietitian_id, id) ON DELETE CASCADE""")
            tx.execute(f"CREATE INDEX {table}_tenant_recipe_idx ON {table}(dietitian_id, recipe_id)")

        # Defense in depth: every Database transaction sets this server-owned ID.
        # Superusers/BYPASSRLS still bypass policies; explicit repository filters
        # remain necessary. Use a normal runtime role for the additional protection.
        for table in TENANT_TABLES:
            tx.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            tx.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            tx.execute(f"""CREATE POLICY tenant_isolation ON {table}
                USING (dietitian_id = current_setting('app.dietitian_id', true))
                WITH CHECK (dietitian_id = current_setting('app.dietitian_id', true))""")
        tx.execute("INSERT INTO recipe_schema_versions(version) VALUES (%s)", (SCHEMA_VERSION,))
    logger.info("Tenant recipe schema is ready (version %s)", SCHEMA_VERSION)
