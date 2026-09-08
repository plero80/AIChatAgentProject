"""HTTP API for the Alona chat UI."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from Config import settings
from Logging import configure_logging
from RateLimiter import SlidingWindowLimiter
from Session import SESSION_COOKIE, resolve_session_id
from main import create_app, get_db_conninfo

configure_logging()
logger = logging.getLogger("alona.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the graph once, before any request can race to create it
    global _graph

    try:
        async with AsyncConnectionPool(
            conninfo=get_db_conninfo(),
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        ) as pool:
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()

            _graph = create_app(checkpointer=checkpointer)
            logger.info(
                "Ready: db=%s model=%s limits=%s/user %s/ip",
                settings.DB_NAME,
                settings.CHAT_MODEL,
                settings.RATE_LIMIT_PER_USER,
                settings.RATE_LIMIT_PER_IP,
            )
            yield
    except Exception:
        logger.exception(
            "Startup failed (check DATABASE_URL and pgvector). HTTP is up so logs are visible."
        )
        _graph = None
        yield

    logger.info("Shutdown complete")


app = FastAPI(title="AlonaAI Chat API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = None

# A chat turn costs several model calls, so keep the ceilings low
user_limiter = SlidingWindowLimiter(
    max_requests=settings.RATE_LIMIT_PER_USER,
    window_seconds=60,
)
ip_limiter = SlidingWindowLimiter(
    max_requests=settings.RATE_LIMIT_PER_IP,
    window_seconds=60,
)


def get_graph():
    if _graph is None:
        raise HTTPException(status_code=503, detail="Service starting up")
    return _graph


def enforce_rate_limits(user_id: str, client_ip: str) -> None:
    for limiter, key in ((user_limiter, user_id), (ip_limiter, client_ip)):
        retry_after = limiter.retry_after(key)

        if retry_after is not None:
            logger.warning(
                "Rate limited: user=%s ip=%s retry_after=%.1fs",
                user_id,
                client_ip,
                retry_after,
            )
            raise HTTPException(
                status_code=429,
                detail="Too many messages. Please wait a moment.",
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )

    user_limiter.record(user_id)
    ip_limiter.record(client_ip)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    reply: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request, response: Response):
    user_id, is_new = resolve_session_id(
        http_request.cookies.get(SESSION_COOKIE)
    )
    if is_new:
        response.set_cookie(
            SESSION_COOKIE,
            user_id,
            httponly=True,
            samesite="lax",
            secure=settings.SESSION_COOKIE_SECURE,
            max_age=60 * 60 * 24 * 365,
        )

    client_ip = http_request.client.host if http_request.client else "unknown"
    enforce_rate_limits(user_id, client_ip)

    started = time.monotonic()

    try:
        result = await get_graph().arun(
            message=request.message.strip(),
            user_id=user_id,
        )

        reply = result.get("final_response") or ""
        analysis = result.get("analysis")

        logger.info(
            "chat user=%s intent=%s mode=%s chars=%d in %.2fs",
            user_id,
            getattr(analysis, "intent", "?"),
            getattr(analysis, "recipe_mode", "?"),
            len(reply),
            time.monotonic() - started,
        )

        return ChatResponse(reply=reply)

    except Exception:
        logger.exception(
            "chat failed user=%s after %.2fs",
            user_id,
            time.monotonic() - started,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to generate response",
        )


# Mounted last so it never shadows the API routes above.
# In dev the Vite server serves the UI instead, and this directory won't exist.
FRONTEND_DIST = Path(__file__).resolve().parent / "Frontend" / "dist"
if not FRONTEND_DIST.is_dir():
    FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DIST, html=True),
        name="frontend",
    )
else:
    logger.info("No frontend build found at %s; serving API only", FRONTEND_DIST)

