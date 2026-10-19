"""HTTP API for the Alona chat UI."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from backend.config.settings import settings
from backend.graphs.graph_requests import ChatGraphRequest
from backend.graphs.base_chat_graph import BaseChatGraph
from backend.factories.service_factories import ServiceFactories
from backend.factories.model_registry import ModelRegistry
from backend.config.models import load_model_config
from backend.config.services import load_service_config
from backend.logging_config import configure_logging
from backend.models.chat_response import ChatResponse
from backend.web.rate_limiter import SlidingWindowLimiter
from backend.services.chat.recipe_presentation_service import build_text_response
from backend.web.session import SESSION_COOKIE, resolve_session_id
from backend.bootstrap import create_app, get_db_conninfo
from backend.paths import DATA_DIR, PROJECT_ROOT

configure_logging()
logger = logging.getLogger("alona.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the graph once, before any request can race to create it
    global _graph

    # Invalid service selections must fail startup, including direct uvicorn use.
    # Resolve them before the database pool and outside the DB recovery handler.
    service_config = load_service_config()
    service_factories = ServiceFactories()
    service_factories.validate(service_config)
    model_config = load_model_config(service_config=service_config)
    ModelRegistry.validate(model_config, service_factories.chat_model)

    try:
        async with AsyncConnectionPool(
            conninfo=get_db_conninfo(),
            min_size=0,
            max_size=10,
            check=AsyncConnectionPool.check_connection,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        ) as pool:
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()

            _graph = create_app(
                checkpointer=checkpointer,
                service_config=service_config,
                service_factories=service_factories,
                model_config=model_config,
            )
            logger.info(
                "Ready: db=%s chat_graph=%s model_assignments=%s limits=%s/user %s/ip",
                settings.DB_NAME,
                service_config.chat_graph,
                model_config.assignments.model_dump(),
                settings.RATE_LIMIT_PER_USER,
                settings.RATE_LIMIT_PER_IP,
            )
            yield
    except Exception:
        logger.exception(
            "Startup failed; check model provider credentials and database configuration. "
            "HTTP is up so logs are visible."
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

_graph: BaseChatGraph | None = None

# A chat turn costs several model calls, so keep the ceilings low
user_limiter = SlidingWindowLimiter(
    max_requests=settings.RATE_LIMIT_PER_USER,
    window_seconds=60,
)
ip_limiter = SlidingWindowLimiter(
    max_requests=settings.RATE_LIMIT_PER_IP,
    window_seconds=60,
)


def get_graph() -> BaseChatGraph:
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
        try:
            result = await get_graph().arun(ChatGraphRequest(
                message=request.message.strip(),
                user_id=user_id,
            ))
        except (pg_errors.AdminShutdown, pg_errors.OperationalError):
            logger.warning("Postgres dropped a connection; retrying once")
            result = await get_graph().arun(ChatGraphRequest(
                message=request.message.strip(),
                user_id=user_id,
            ))

        reply = result.get("final_response") or ""
        blocks = result.get("response_blocks")
        presentation = (
            ChatResponse(reply=reply, blocks=blocks)
            if blocks is not None
            else build_text_response(reply)
        )
        analysis = result.get("analysis")

        logger.info(
            "chat user=%s intent=%s mode=%s chars=%d in %.2fs",
            user_id,
            getattr(analysis, "intent", "?"),
            getattr(analysis, "recipe_mode", "?"),
            len(reply),
            time.monotonic() - started,
        )

        return presentation

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


RECIPE_IMAGES = DATA_DIR / "recipe_images"
if RECIPE_IMAGES.is_dir():
    app.mount(
        "/recipe-images",
        StaticFiles(directory=RECIPE_IMAGES),
        name="recipe-images",
    )


# Mounted last so it never shadows the API routes above.
# In dev the Vite server serves the UI instead, and this directory won't exist.
FRONTEND_DIST = PROJECT_ROOT / "Frontend" / "dist"
if not FRONTEND_DIST.is_dir():
    FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DIST, html=True),
        name="frontend",
    )
else:
    logger.info("No frontend build found at %s; serving API only", FRONTEND_DIST)
