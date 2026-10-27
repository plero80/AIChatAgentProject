"""Launcher for the chat API.

psycopg's async mode requires a selector loop on Windows.
"""

import os
import sys

import uvicorn

from backend.config.settings import settings

if __name__ == "__main__":
    from backend.cli.options import configure_service_cli

    if os.getenv("CLIENT_CONFIG_SOURCE", "file") == "database":
        import argparse
        argparse.ArgumentParser(description="Run published client releases. Manage configuration with backend.clients.manage.").parse_args()
    elif not configure_service_cli():
        raise SystemExit(0)

    port = int(os.environ.get("PORT", settings.API_PORT))
    host = os.environ.get("API_HOST", settings.API_HOST)
    # Railway must reach the process from outside the container
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT"):
        host = "0.0.0.0"

    print(f"Listening on {host}:{port}", flush=True)
    uvicorn.run(
        "backend.web.api:app",
        host=host,
        port=port,
        reload=False if os.environ.get("PORT") else settings.API_RELOAD,
        # Current Uvicorn selects its own factory; an event-loop policy is not enough.
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
    )
