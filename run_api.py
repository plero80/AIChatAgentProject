"""Launcher for the chat API.

psycopg's async mode cannot run on Windows' default ProactorEventLoop, so the
selector policy has to be installed before uvicorn creates its event loop.
"""

import asyncio
import sys

import uvicorn

from Config import settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.API_RELOAD,
    )
