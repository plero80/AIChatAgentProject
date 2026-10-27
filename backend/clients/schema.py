"""Additive catalog migration, separate from recipe storage and checkpoints."""

CATALOG_VERSION = 1

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS client_catalog_versions (version INTEGER PRIMARY KEY)""",
    """CREATE TABLE clients (
        id TEXT PRIMARY KEY CHECK (id ~ '^[a-z][a-z0-9_]{0,63}$'),
        slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9-]{0,63}$'),
        business_type TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        active_release_id UUID,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE client_releases (
        id UUID PRIMARY KEY,
        client_id TEXT NOT NULL REFERENCES clients(id),
        version INTEGER NOT NULL CHECK (version > 0),
        config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
        fingerprint TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (client_id, version), UNIQUE (client_id, id),
        CHECK (config->>'client_id' IS NOT NULL AND config->>'client_id' = client_id)
    )""",
    """ALTER TABLE clients ADD CONSTRAINT active_release_owner
        FOREIGN KEY (id, active_release_id) REFERENCES client_releases(client_id, id)""",
    """CREATE TABLE client_prompt_versions (
        client_id TEXT NOT NULL REFERENCES clients(id),
        name TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        template TEXT NOT NULL,
        PRIMARY KEY (client_id, name, version)
    )""",
    """CREATE TABLE conversations (
        client_id TEXT NOT NULL REFERENCES clients(id),
        session_id TEXT NOT NULL CHECK (length(session_id) BETWEEN 1 AND 256),
        release_id UUID NOT NULL,
        thread_id TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (client_id, session_id),
        FOREIGN KEY (client_id, release_id) REFERENCES client_releases(client_id, id),
        CHECK (thread_id = client_id || ':' || session_id)
    )""",
    "CREATE INDEX conversations_release_idx ON conversations(client_id, release_id)",
    """CREATE FUNCTION reject_catalog_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Published releases and conversation pins are immutable'; END $$""",
    """CREATE TRIGGER immutable_release BEFORE UPDATE OR DELETE ON client_releases
        FOR EACH ROW EXECUTE FUNCTION reject_catalog_mutation()""",
    """CREATE TRIGGER immutable_prompt BEFORE UPDATE OR DELETE ON client_prompt_versions
        FOR EACH ROW EXECUTE FUNCTION reject_catalog_mutation()""",
    """CREATE TRIGGER immutable_conversation BEFORE UPDATE ON conversations
        FOR EACH ROW EXECUTE FUNCTION reject_catalog_mutation()""",
    """CREATE FUNCTION protect_client_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.slug <> OLD.slug OR NEW.business_type <> OLD.business_type THEN
            RAISE EXCEPTION 'Client identity and URL are immutable';
          END IF;
          RETURN NEW;
        END $$""",
    """CREATE TRIGGER immutable_client_identity BEFORE UPDATE ON clients
        FOR EACH ROW EXECUTE FUNCTION protect_client_identity()""",
)


async def apply_catalog_schema(pool):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(740291830125)")
            found = await (await connection.execute("SELECT to_regclass('client_catalog_versions')")).fetchone()
            if found[0]:
                row = await (await connection.execute("SELECT MAX(version) FROM client_catalog_versions")).fetchone()
                if row[0] == CATALOG_VERSION:
                    return
                if row[0] is not None:
                    raise RuntimeError("Unsupported client catalog version")
            for statement in STATEMENTS:
                await connection.execute(statement)
            await connection.execute("INSERT INTO client_catalog_versions VALUES (%s)", (CATALOG_VERSION,))
