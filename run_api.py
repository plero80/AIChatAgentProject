"""Launcher for the chat API.

psycopg's async mode cannot run on Windows' default ProactorEventLoop, so the
selector policy has to be installed before uvicorn creates its event loop.
"""

import asyncio
import os
import sys

import uvicorn

from Config import settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.API_PORT))
    host = os.environ.get("API_HOST", settings.API_HOST)
    # Railway must reach the process from outside the container
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"):
        host = "0.0.0.0"

    print(f"Listening on {host}:{port}", flush=True)
    uvicorn.run(
        "api:app",
        host=host,
        port=port,
        reload=False if os.environ.get("PORT") else settings.API_RELOAD,
    )
