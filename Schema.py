"""Create recipe tables on a fresh Postgres (Railway, local, etc.)."""

import logging

logger = logging.getLogger("alona.schema")

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS recipes (
        id UUID PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        instructions TEXT,
        instagram_url TEXT,
        image_url TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipe_ingredients (
        id UUID PRIMARY KEY,
        recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        ingredient_name TEXT NOT NULL,
        quantity DOUBLE PRECISION,
        unit TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recipe_embeddings (
        recipe_id UUID NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        embedding vector(1536) NOT NULL,
        model TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS image_url TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS recipes_name_key ON recipes (name)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS recipes_instagram_url_key
        ON recipes (instagram_url)
        WHERE instagram_url IS NOT NULL
    """,
]


def apply_schema(db) -> None:
    for sql in STATEMENTS:
        db.execute(sql)
    logger.info("Recipe schema applied")
