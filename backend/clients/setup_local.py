"""Create the Alona and Michael URL clients in the configured database, once."""

import asyncio
import sys

from psycopg_pool import AsyncConnectionPool

from backend.clients.catalog import PostgresClientCatalog
from backend.clients.manage import export_legacy, read_release
from backend.clients.schema import apply_catalog_schema
from backend.config.settings import settings
from backend.paths import CONFIG_DIR


async def ensure_client(catalog, spec, *, slug, business_type):
    """Rerunning setup preserves active releases, history and existing pins."""
    async with catalog.pool.connection() as connection:
        row = await (await connection.execute(
            "SELECT slug, business_type, enabled, active_release_id FROM clients WHERE id = %s",
            (spec.client_id,),
        )).fetchone()
    if row is None:
        await catalog.create_client(spec.client_id, slug, business_type)
    elif row[:2] != (slug, business_type) or not row[2]:
        raise ValueError(f"Existing client {spec.client_id} has a different identity or is disabled")
    elif row[3] is not None:
        return await catalog.get_release(spec.client_id, str(row[3]))
    return await catalog.publish(spec, activate=True, adopt_legacy=(spec.client_id == "clinic_001"))


async def setup():
    # Validate both complete documents before connecting or creating catalog data.
    alona = export_legacy("clinic_001")
    michael = read_release(CONFIG_DIR / "releases" / "michael_lipkin.json")
    async with AsyncConnectionPool(
        settings.db_conninfo(), min_size=0, max_size=2, timeout=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "connect_timeout": 5},
    ) as pool:
        await apply_catalog_schema(pool)
        catalog = PostgresClientCatalog(pool)
        for spec, slug, business_type in ((alona, "alona", "dietitian"),
                                          (michael, "michael-lipkin", "cooking_assistant")):
            release = await ensure_client(catalog, spec, slug=slug, business_type=business_type)
            print(f"Ready: /c/{slug} -> {spec.client_id}, release v{release.version}")


def main():
    asyncio.run(setup(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
