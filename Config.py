"""Environment-driven settings. Defaults match local development."""

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else default


class Settings:

    # Database
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = _int("DB_PORT", 5432)
    DB_NAME = os.getenv("DB_NAME", "recipes_db")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
    DB_POOL_MIN = _int("DB_POOL_MIN", 2)
    DB_POOL_MAX = _int("DB_POOL_MAX", 20)

    # Models
    CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4-mini")
    CHAT_MAX_TOKENS = _int("CHAT_MAX_TOKENS", 16000)
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    # API
    CORS_ORIGINS = _list(
        "CORS_ORIGINS",
        ["http://localhost:5173", "http://127.0.0.1:5173"],
    )
    API_HOST = os.getenv("API_HOST", "127.0.0.1")
    API_PORT = _int("API_PORT", 8000)
    API_RELOAD = os.getenv("API_RELOAD", "true").lower() == "true"

    # Rate limits (requests per minute)
    RATE_LIMIT_PER_USER = _int("RATE_LIMIT_PER_USER", 15)
    RATE_LIMIT_PER_IP = _int("RATE_LIMIT_PER_IP", 40)

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"

    @classmethod
    def db_conninfo(cls) -> str:
        return (
            f"host={cls.DB_HOST} port={cls.DB_PORT} dbname={cls.DB_NAME} "
            f"user={cls.DB_USER} password={cls.DB_PASSWORD}"
        )


settings = Settings()
