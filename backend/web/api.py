"""FastAPI entry point with database routing and an explicit legacy file mode."""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from backend.config.settings import settings
from backend.workflows.contracts import ChatWorkflow, ConversationRequest
from backend.workflows.facade import WorkflowFacade
from backend.factories.service_factories import ServiceFactories
from backend.factories.model_registry import ModelRegistry
from backend.config.models import load_model_config
from backend.config.services import load_service_config
from backend.config.guard import load_guard_config
from backend.config.images import load_image_config, validate_image_config
from backend.logging_config import configure_logging
from backend.models.chat_response import ChatResponse
from backend.models.chat_input import ChatContent, InvalidChatImage, image_upload_capabilities
from backend.web.body_limit import ChatBodyLimit
from backend.workflows.image_input import ImageInputUnsupported, ImageRecognitionUnavailable
from backend.web.rate_limiter import SlidingWindowLimiter
from backend.web.session import SESSION_COOKIE, resolve_session_id
from backend.bootstrap import client_for_tenant, create_workflow_factory, get_db_conninfo
from backend.paths import PROJECT_ROOT
from backend.tenancy import TenantContext, load_tenant

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.config_source == "database":
        from backend.web.client_routes import database_lifespan
        async with database_lifespan(app):
            yield
        return
    tenant = app.state.configured_tenant or load_tenant()
    app.state.tenant = tenant
    app.state.workflow = None
    app.state.workflow_facade = None

    # Invalid service selections must fail startup, including direct uvicorn use.
    # Resolve them before opening the database pool.
    service_config = load_service_config()
    service_factories = ServiceFactories()
    service_factories.validate(service_config)
    model_config = load_model_config(service_config=service_config)
    ModelRegistry.validate(model_config, service_factories.chat_model)
    guard_config = load_guard_config(tenant)
    service_factories.guard_evaluator.validate(guard_config.evaluator)
    image_config = load_image_config()
    validate_image_config(image_config, service_factories)

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

        factory = create_workflow_factory(
            checkpointer=checkpointer,
            tenant=tenant,
            service_config=service_config,
            service_factories=service_factories,
            model_config=model_config,
            guard_config=guard_config,
            image_config=image_config,
            graph_factory=app.state.graph_factory,
        )
        client = client_for_tenant(tenant)
        # Exit the facade (drain turns, close runtimes) before the checkpoint pool.
        async with WorkflowFacade(factory) as facade:
            await facade.warm(client)
            app.state.workflow_facade = facade
            app.state.workflow = facade.for_client(client)
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
                app.state.workflow = None
                app.state.workflow_facade = None
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


def get_workflow(request: Request) -> ChatWorkflow:
    workflow = getattr(request.app.state, "workflow", None)
    if workflow is None:
        raise HTTPException(status_code=503, detail="Service starting up")
    return workflow


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


class ChatRequest(ChatContent):
    """Public JSON boundary shared by file and database routes."""


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request, response: Response):
    workflow = get_workflow(http_request)
    client = workflow.client
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
    enforce_rate_limits(f"{client.client_id}:{user_id}", f"{client.client_id}:{client_ip}")

    started = time.monotonic()
    turn = ConversationRequest(
        message=request.message,
        images=request.images,
        conversation_id=user_id,
        client_id=client.client_id,
        release_id=client.release_id,
    )

    try:
        # A whole-turn retry would repeat admission and could replay checkpointed
        # work. Retry only with explicit idempotency/resume support in a future step.
        presentation = await workflow.respond(turn)

        logger.info(
            "chat client=%s user=%s release=%s chars=%d in %.2fs",
            client.client_id,
            user_id,
            client.release_id,
            len(presentation.reply),
            time.monotonic() - started,
        )

        return presentation

    except HTTPException:
        raise
    except (ImageInputUnsupported, InvalidChatImage) as exc:
        raise HTTPException(422, str(exc)) from None
    except ImageRecognitionUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except Exception:
        logger.exception(
            "chat failed client=%s user=%s after %.2fs",
            client.client_id,
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
    return {**get_tenant(request).public_config(), "image_upload": image_upload_capabilities(True)}


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


def create_http_app(*, tenant: TenantContext | None = None, graph_factory=None,
                    config_source=None, catalog=None, checkpointer=None, release_factories=None) -> FastAPI:
    """Same application code; each instance retains its own startup context."""
    application = FastAPI(title="Dietitian Recipe Chat API", lifespan=lifespan)
    application.state.configured_tenant = tenant
    application.state.graph_factory = graph_factory
    source = config_source or ("database" if catalog is not None else os.getenv("CLIENT_CONFIG_SOURCE", "file"))
    if source not in {"file", "database"}:
        raise ValueError("CLIENT_CONFIG_SOURCE must be file or database")
    if source == "database" and (tenant is not None or graph_factory is not None):
        raise ValueError("Database mode resolves its graphs and clients from published releases")
    application.state.config_source = source
    application.state.configured_catalog = catalog
    application.state.configured_checkpointer = checkpointer
    application.state.release_factories = release_factories
    application.add_middleware(ChatBodyLimit)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # FastAPI's default includes input values, which would echo uploaded base64.
        details = [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]
        return JSONResponse(status_code=422, content={"detail": details})
    application.add_middleware(
        CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    application.include_router(router)
    from backend.web.client_routes import router as client_router, chat_for_client, client_page

    @application.post("/api/clients/{slug}/chat", response_model=ChatResponse)
    async def client_chat(slug: str, body: ChatRequest, request: Request, response: Response):
        return await chat_for_client(slug, body, request, response)

    application.include_router(client_router)
    application.add_api_route("/c/{slug}", client_page, methods=["GET"], include_in_schema=False)
    # Mounted last so it never shadows the API routes.
    frontend_dist = PROJECT_ROOT / "Frontend" / "dist"
    if frontend_dist.is_dir():
        application.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return application


app = create_http_app()
