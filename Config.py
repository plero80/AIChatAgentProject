"""Environment-driven settings. Defaults match local development."""

import os
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else default


def _on_railway() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("PORT"))


def _db_from_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": (parsed.path or "/railway").lstrip("/") or "railway",
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
    }


class Settings:

    _url = os.getenv("DATABASE_URL")
    _parsed = _db_from_url(_url) if _url else None

    # DATABASE_URL (Railway) wins over leftover local DB_HOST=localhost values
    DB_HOST = (
        (_parsed["host"] if _parsed else None)
        or os.getenv("DB_HOST")
        or os.getenv("PGHOST")
        or "localhost"
    )
    DB_PORT = int(
        (_parsed["port"] if _parsed else None)
        or os.getenv("DB_PORT")
        or os.getenv("PGPORT")
        or 5432
    )
    DB_NAME = (
        (_parsed["dbname"] if _parsed else None)
        or os.getenv("DB_NAME")
        or os.getenv("PGDATABASE")
        or "recipes_db"
    )
    DB_USER = (
        (_parsed["user"] if _parsed else None)
        or os.getenv("DB_USER")
        or os.getenv("PGUSER")
        or "postgres"
    )
    DB_PASSWORD = (
        (_parsed["password"] if _parsed else None)
        or os.getenv("POSTGRES_PASSWORD")
        or os.getenv("PGPASSWORD")
    )
    DB_POOL_MIN = _int("DB_POOL_MIN", 2)
    DB_POOL_MAX = _int("DB_POOL_MAX", 20)

    CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4-mini")
    CHAT_MAX_TOKENS = _int("CHAT_MAX_TOKENS", 16000)
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    CORS_ORIGINS = _list(
        "CORS_ORIGINS",
        ["http://localhost:5173", "http://127.0.0.1:5173"],
    )
    API_HOST = os.getenv("API_HOST") or ("0.0.0.0" if _on_railway() else "127.0.0.1")
    API_PORT = int(os.getenv("PORT") or os.getenv("API_PORT") or (8080 if _on_railway() else 8000))
    API_RELOAD = os.getenv("API_RELOAD", "false" if _on_railway() else "true").lower() == "true"

    RATE_LIMIT_PER_USER = _int("RATE_LIMIT_PER_USER", 15)
    RATE_LIMIT_PER_IP = _int("RATE_LIMIT_PER_IP", 40)

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    SESSION_COOKIE_SECURE = os.getenv(
        "SESSION_COOKIE_SECURE",
        "true" if _on_railway() else "false",
    ).lower() == "true"

    @classmethod
    def db_conninfo(cls) -> str:
        return (
            f"host={cls.DB_HOST} port={cls.DB_PORT} dbname={cls.DB_NAME} "
            f"user={cls.DB_USER} password={cls.DB_PASSWORD}"
        )


settings = Settings()
