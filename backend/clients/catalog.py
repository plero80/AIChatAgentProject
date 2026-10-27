"""PostgreSQL client lookup, immutable publication and atomic conversation pinning."""

from contextlib import asynccontextmanager
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from backend.clients.releases import ClientRecord, PublishedRelease, ReleaseSpec, Slug
from backend.clients.schema import CATALOG_VERSION
from backend.config.workflows import Name


class ClientNotFound(LookupError):
    pass


class ReleaseUnavailable(LookupError):
    pass


class PostgresClientCatalog:
    def __init__(self, pool, *, lock_pool=None):
        self.pool = pool
        # Lock holders/waiters must not exhaust metadata or checkpoint connections.
        self.lock_pool = lock_pool if lock_pool is not None else pool

    async def check_schema(self):
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT to_regclass('client_catalog_versions') AS name")
                if not (await cursor.fetchone())["name"]:
                    raise RuntimeError("Client catalog is missing; run python -m backend.clients.manage migrate")
                await cursor.execute("SELECT MAX(version) AS version FROM client_catalog_versions")
                if (await cursor.fetchone())["version"] != CATALOG_VERSION:
                    raise RuntimeError("Client catalog version does not match this application")

    @staticmethod
    def _client(row):
        if row is None:
            raise ClientNotFound("Unknown or disabled client")
        return ClientRecord(row["id"], row["slug"], row["business_type"],
                            str(row["active_release_id"]) if row["active_release_id"] else None)

    @staticmethod
    def _release(client, row):
        if row is None:
            raise ReleaseUnavailable("Client release is unavailable")
        spec = ReleaseSpec.model_validate(row["config"])
        if spec.client_id != client.client_id or spec.fingerprint() != row["fingerprint"]:
            raise ReleaseUnavailable("Release snapshot integrity check failed")
        return PublishedRelease(client, str(row["id"]), row["version"], spec)

    async def lookup(self, slug: str) -> ClientRecord:
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT * FROM clients WHERE slug = %s AND enabled", (slug,))
                return self._client(await cursor.fetchone())

    async def get_release(self, client_id: str, release_id: str) -> PublishedRelease:
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT * FROM clients WHERE id = %s AND enabled", (client_id,))
                client = self._client(await cursor.fetchone())
                await cursor.execute(
                    "SELECT * FROM client_releases WHERE client_id = %s AND id = %s",
                    (client_id, release_id),
                )
                return self._release(client, await cursor.fetchone())

    async def pin(self, slug: str, session_id: str) -> PublishedRelease:
        """Commit identity before graph execution; never update an existing pin."""
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 256:
            raise ValueError("Invalid conversation identity")
        async with self.pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    # Publication/activation takes FOR UPDATE on this same row.
                    await cursor.execute("SELECT * FROM clients WHERE slug = %s AND enabled FOR SHARE", (slug,))
                    client = self._client(await cursor.fetchone())
                    if client.active_release_id is None:
                        raise ReleaseUnavailable("Client has no active release")
                    await cursor.execute(
                        """INSERT INTO conversations(client_id, session_id, release_id, thread_id)
                        VALUES (%s, %s, %s, %s) ON CONFLICT (client_id, session_id) DO NOTHING""",
                        (client.client_id, session_id, client.active_release_id, f"{client.client_id}:{session_id}"),
                    )
                    await cursor.execute(
                        """SELECT r.* FROM conversations c JOIN client_releases r
                        ON r.client_id = c.client_id AND r.id = c.release_id
                        WHERE c.client_id = %s AND c.session_id = %s""",
                        (client.client_id, session_id),
                    )
                    return self._release(client, await cursor.fetchone())

    @asynccontextmanager
    async def turn_lock(self, client_id: str, session_id: str):
        """Serialize one thread across workers; rollback/cancellation releases the lock."""
        async with self.lock_pool.connection() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL lock_timeout = '10s'")
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 740291830126))",
                    (f"{client_id}:{session_id}",),
                )
                yield

    async def create_client(self, client_id: str, slug: str, business_type: str):
        TypeAdapter(Name).validate_python(client_id)
        TypeAdapter(Slug).validate_python(slug)
        TypeAdapter(Name).validate_python(business_type)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO clients(id, slug, business_type) VALUES (%s, %s, %s)",
                    (client_id, slug, business_type),
                )

    async def publish(self, spec: ReleaseSpec, *, factories=None, activate=False, adopt_legacy=False):
        from backend.workflows.release_runtime import validate_release

        # Snapshot and validate before opening the publication transaction.
        spec = ReleaseSpec.model_validate(spec.model_dump(mode="json", by_alias=True))
        validate_release(spec, factories=factories)
        async with self.pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("SELECT * FROM clients WHERE id = %s FOR UPDATE", (spec.client_id,))
                    client = self._client(await cursor.fetchone())
                    await cursor.execute(
                        "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM client_releases WHERE client_id = %s",
                        (spec.client_id,),
                    )
                    version = (await cursor.fetchone())["version"]
                    if adopt_legacy and version != 1:
                        raise ValueError("Legacy checkpoints can only be adopted by the initial release")
                    for name, prompt in spec.prompts.items():
                        await cursor.execute(
                            """INSERT INTO client_prompt_versions(client_id, name, version, template)
                            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                            (spec.client_id, name, prompt.version, prompt.text),
                        )
                        await cursor.execute(
                            "SELECT template FROM client_prompt_versions WHERE client_id = %s AND name = %s AND version = %s",
                            (spec.client_id, name, prompt.version),
                        )
                        if (await cursor.fetchone())["template"] != prompt.text:
                            raise ValueError(f"Prompt {name!r} changed: increment its version before publishing")
                    release_id = str(uuid4())
                    await cursor.execute(
                        """INSERT INTO client_releases(id, client_id, version, config, fingerprint)
                        VALUES (%s, %s, %s, %s, %s)""",
                        (release_id, spec.client_id, version,
                         Jsonb(spec.model_dump(mode="json", by_alias=True)), spec.fingerprint()),
                    )
                    if any(node.kind == "recipe_execute" for node in spec.workflow.nodes):
                        # Compatibility ownership row for existing recipe foreign keys.
                        # New non-recipe businesses do not need the recipe tables.
                        await cursor.execute(
                            "INSERT INTO dietitians(id, display_name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                            (spec.client_id, spec.profile.display_name),
                        )
                    if adopt_legacy:
                        await cursor.execute("SELECT to_regclass('checkpoints') AS name")
                        if (await cursor.fetchone())["name"]:
                            prefix = spec.client_id + ":"
                            await cursor.execute(
                                """INSERT INTO conversations(client_id, session_id, release_id, thread_id)
                                SELECT DISTINCT %s, substring(thread_id FROM %s), %s::uuid, thread_id
                                FROM checkpoints WHERE left(thread_id, %s) = %s AND length(thread_id) > %s
                                ON CONFLICT (client_id, session_id) DO NOTHING""",
                                (spec.client_id, len(prefix) + 1, release_id, len(prefix), prefix, len(prefix)),
                            )
                    if activate:
                        await cursor.execute("UPDATE clients SET active_release_id = %s WHERE id = %s",
                                             (release_id, spec.client_id))
                    return PublishedRelease(client, release_id, version, spec)

    async def activate(self, client_id: str, release_id: str):
        """Activation and rollback use the same operation; existing pins are untouched."""
        async with self.pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("SELECT id FROM clients WHERE id = %s FOR UPDATE", (client_id,))
                    if await cursor.fetchone() is None:
                        raise ClientNotFound("Unknown client")
                    await cursor.execute("SELECT id FROM client_releases WHERE client_id = %s AND id = %s",
                                         (client_id, release_id))
                    if await cursor.fetchone() is None:
                        raise ReleaseUnavailable("Release does not belong to this client")
                    await cursor.execute("UPDATE clients SET active_release_id = %s WHERE id = %s",
                                         (release_id, client_id))

    async def list_releases(self, client_id: str):
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """SELECT r.id, r.version, r.fingerprint, r.created_at,
                    (c.active_release_id = r.id) AS active
                    FROM client_releases r JOIN clients c ON c.id = r.client_id
                    WHERE r.client_id = %s ORDER BY r.version""",
                    (client_id,),
                )
                return await cursor.fetchall()
