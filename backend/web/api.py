"""One configurable FastAPI deployment per dietitian, using shared route code."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import errors as pg_errors
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from backend.paths import PROJECT_ROOT
from backend.tenancy import TenantContext, load_tenant

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tenant = app.state.configured_tenant or load_tenant()
    app.state.tenant = tenant
    app.state.graph = None

    # Invalid service selections must fail startup, including direct uvicorn use.
    # Resolve them before opening the database pool.
    service_config = load_service_config()
    service_factories = ServiceFactories()
    service_factories.validate(service_config)
    model_config = load_model_config(service_config=service_config)
    ModelRegistry.validate(model_config, service_factories.chat_model)

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

        app.state.graph = (app.state.graph_factory or create_app)(
            checkpointer=checkpointer,
            tenant=tenant,
            service_config=service_config,
            service_factories=service_factories,
            model_config=model_config,
        )
        logger.info(
            "Ready: dietitian=%s db=%s chat_graph=%s model_assignments=%s limits=%s/user %s/ip",
            tenant.dietitian_id,
            settings.DB_NAME,
            service_config.chat_graph,
            model_config.assignments.model_dump(),
            settings.RATE_LIMIT_PER_USER,
            settings.RATE_LIMIT_PER_IP,
        )
        try:
            yield
        finally:
            app.state.graph = None
    logger.info("Shutdown complete")


router = APIRouter()

# A chat turn costs several model calls, so keep the ceilings low
user_limiter = SlidingWindowLimiter(
    max_requests=settings.RATE_LIMIT_PER_USER,
    window_seconds=60,
)
ip_limiter = SlidingWindowLimiter(
    max_requests=settings.RATE_LIMIT_PER_IP,
    window_seconds=60,
)


def get_graph(request: Request) -> BaseChatGraph:
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="Service starting up")
    return graph


def get_tenant(request: Request) -> TenantContext:
    tenant = getattr(request.app.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=503, detail="Service starting up")
    return tenant


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
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must contain non-whitespace text")
        return value.strip()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request, response: Response):
    tenant = get_tenant(http_request)
    graph = get_graph(http_request)
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
    enforce_rate_limits(f"{tenant.dietitian_id}:{user_id}", f"{tenant.dietitian_id}:{client_ip}")

    started = time.monotonic()

    try:
        try:
            result = await graph.arun(ChatGraphRequest(
                message=request.message.strip(),
                user_id=user_id,
                dietitian_id=tenant.dietitian_id,
            ))
        except (pg_errors.AdminShutdown, pg_errors.OperationalError):
            logger.warning("Postgres dropped a connection; retrying once")
            result = await graph.arun(ChatGraphRequest(
                message=request.message.strip(),
                user_id=user_id,
                dietitian_id=tenant.dietitian_id,
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
            "chat dietitian=%s user=%s intent=%s mode=%s chars=%d in %.2fs",
            tenant.dietitian_id,
            user_id,
            getattr(analysis, "intent", "?"),
            getattr(analysis, "recipe_mode", "?"),
            len(reply),
            time.monotonic() - started,
        )

        return presentation

    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "chat failed dietitian=%s user=%s after %.2fs",
            tenant.dietitian_id,
            user_id,
            time.monotonic() - started,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to generate response",
        )


@router.get("/public-config")
def public_config(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return get_tenant(request).public_config()


@router.get("/recipe-images/{filename}")
def recipe_image(filename: str, request: Request):
    directory = get_tenant(request).images_dir.resolve()
    try:
        image = (directory / filename).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Image not found") from None
    # Only a direct file in this deployment's image folder, including on Windows.
    if image.parent != directory or image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not image.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image)


def create_http_app(*, tenant: TenantContext | None = None, graph_factory=None) -> FastAPI:
    """Same application code; each instance retains its own startup context."""
    application = FastAPI(title="Dietitian Recipe Chat API", lifespan=lifespan)
    application.state.configured_tenant = tenant
    application.state.graph_factory = graph_factory
    application.add_middleware(
        CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    application.include_router(router)
    # Mounted last so it never shadows the API routes.
    frontend_dist = PROJECT_ROOT / "Frontend" / "dist"
    if frontend_dist.is_dir():
        application.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return application


app = create_http_app()
