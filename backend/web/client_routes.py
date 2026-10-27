"""Multi-client HTTP routes. Browser input selects a URL, never runtime bindings."""

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.errors import LockNotAvailable
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from backend.clients.catalog import ClientNotFound, PostgresClientCatalog, ReleaseUnavailable
from backend.config.settings import settings
from backend.models.chat_input import InvalidChatImage
from backend.paths import PROJECT_ROOT
from backend.web.session import SESSION_COOKIE, resolve_session_id
from backend.workflows.contracts import ConversationRequest
from backend.workflows.checkpoint_types import checkpoint_serializer
from backend.workflows.image_input import ImageInputUnsupported, ImageRecognitionUnavailable
from backend.workflows.facade import WorkflowFacade
from backend.workflows.release_runtime import create_release_workflow_factory, image_directory


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/clients/{slug}")


@asynccontextmanager
async def database_lifespan(app):
    async with AsyncExitStack() as resources:
        catalog = app.state.configured_catalog
        checkpointer = app.state.configured_checkpointer
        if catalog is None:
            from backend.bootstrap import get_db_conninfo

            async def pool(size):
                return await resources.enter_async_context(AsyncConnectionPool(
                    conninfo=get_db_conninfo(), min_size=0, max_size=size,
                    check=AsyncConnectionPool.check_connection,
                    kwargs={"autocommit": True, "prepare_threshold": 0},
                ))

            metadata_pool = await pool(10)
            lock_pool = await pool(10)
            checkpoint_pool = await pool(10)
            catalog = PostgresClientCatalog(metadata_pool, lock_pool=lock_pool)
            checkpointer = AsyncPostgresSaver(checkpoint_pool, serde=checkpoint_serializer())
            await checkpointer.setup()
        await catalog.check_schema()
        if checkpointer is None:
            raise ValueError("A supplied catalog requires a supplied checkpointer")
        factory = create_release_workflow_factory(
            catalog, checkpointer, factories=app.state.release_factories,
        )
        facade = await resources.enter_async_context(WorkflowFacade(factory))
        app.state.catalog = catalog
        app.state.workflow_facade = facade
        app.state.workflow = None
        logger.info("Database-backed client routing ready")
        try:
            yield
        finally:
            app.state.catalog = None
            app.state.workflow_facade = None


def get_catalog(request):
    catalog = getattr(request.app.state, "catalog", None)
    if catalog is None:
        raise HTTPException(503, "Database client routing is not configured")
    return catalog


def session(request, response):
    session_id, is_new = resolve_session_id(request.cookies.get(SESSION_COOKIE))
    if is_new:
        response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax",
                            secure=settings.SESSION_COOKIE_SECURE, max_age=60 * 60 * 24 * 365)
    return session_id


async def pinned_release(catalog, slug, session_id):
    try:
        return await catalog.pin(slug, session_id)
    except ClientNotFound:
        raise HTTPException(404, "Unknown client") from None
    except ReleaseUnavailable:
        raise HTTPException(503, "Client configuration unavailable") from None


@router.get("/public-config")
async def public_config(slug: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    release = await pinned_release(get_catalog(request), slug, session(request, response))
    return release.public_config()


# Registered from api.py after ChatRequest is defined, to keep a single body contract.
async def chat_for_client(slug, body, request, response):
    from backend.web.api import enforce_rate_limits

    catalog = get_catalog(request)
    session_id = session(request, response)
    try:
        client = await catalog.lookup(slug)
    except ClientNotFound:
        raise HTTPException(404, "Unknown client") from None
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limits(f"{client.client_id}:{session_id}", f"{client.client_id}:{client_ip}")
    release = await pinned_release(catalog, slug, session_id)
    turn = ConversationRequest(message=body.message, images=body.images, client_id=client.client_id,
                               conversation_id=session_id, release_id=release.release_id)
    try:
        async with catalog.turn_lock(client.client_id, session_id):
            # Recheck enabled status after a potentially queued lock acquisition.
            await catalog.lookup(slug)
            return await request.app.state.workflow_facade.respond(release.context, turn)
    except ClientNotFound:
        raise HTTPException(404, "Unknown client") from None
    except (ImageInputUnsupported, InvalidChatImage) as exc:
        raise HTTPException(422, str(exc)) from None
    except ImageRecognitionUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except (LockNotAvailable, PoolTimeout):
        raise HTTPException(503, "Conversation is busy. Please try again.", headers={"Retry-After": "2"}) from None
    except Exception as exc:
        # Do not log prompt bodies or configuration validation values.
        logger.error("chat failed client=%s release=%s error=%s", client.client_id, release.release_id, type(exc).__name__)
        raise HTTPException(500, "Failed to generate response") from None


@router.get("/recipe-images/{filename}")
async def recipe_image(slug: str, filename: str, request: Request):
    try:
        client = await get_catalog(request).lookup(slug)
    except ClientNotFound:
        raise HTTPException(404, "Unknown client") from None
    directory = image_directory(client.client_id).resolve()
    try:
        image = (directory / filename).resolve()
    except (OSError, ValueError):
        raise HTTPException(404, "Image not found") from None
    if image.parent != directory or image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not image.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(image)


async def client_page(slug: str):
    index = PROJECT_ROOT / "Frontend" / "dist" / "index.html"
    if not index.is_file():
        raise HTTPException(503, "Frontend has not been built")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
