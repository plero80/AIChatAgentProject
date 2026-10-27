"""Run both example clients against a private, project-local PostgreSQL database.

This development launcher never connects to DATABASE_URL from your .env.
Provider credentials are still loaded normally by the API from .env.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local" / "client-preview"
CLUSTER = LOCAL / "postgres"
STATE = LOCAL / "server.json"
DATABASE = "chat_clients_dev"
DB_USER = "chat_dev"


def postgres_binaries():
    configured = os.getenv("LOCAL_POSTGRES_BIN")
    if configured:
        directory = Path(configured)
    elif sys.platform == "win32" and Path("C:/Program Files/PostgreSQL/16/bin/initdb.exe").is_file():
        directory = Path("C:/Program Files/PostgreSQL/16/bin")
    else:
        executable = shutil.which("initdb")
        if not executable:
            raise RuntimeError("Install PostgreSQL with pgvector, or set LOCAL_POSTGRES_BIN to its bin folder")
        directory = Path(executable).parent
    suffix = ".exe" if sys.platform == "win32" else ""
    paths = {name: directory / (name + suffix) for name in ("initdb", "pg_ctl")}
    if not all(path.is_file() for path in paths.values()):
        raise RuntimeError("LOCAL_POSTGRES_BIN must contain initdb and pg_ctl")
    return paths


def pg_command(*arguments, check=True):
    LOCAL.mkdir(parents=True, exist_ok=True)
    # PostgreSQL's server child must not inherit a captured output pipe on Windows.
    with (LOCAL / "commands.log").open("a", encoding="utf-8") as log:
        result = subprocess.run(
            [str(argument) for argument in arguments], stdout=log, stderr=subprocess.STDOUT,
            timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    if check and result.returncode:
        raise RuntimeError(f"PostgreSQL command failed; inspect {LOCAL / 'commands.log'}")
    return result.returncode


def prepare_database(port):
    binaries = postgres_binaries()
    if not (CLUSTER / "PG_VERSION").is_file():
        pg_command(binaries["initdb"], "-D", CLUSTER, "-U", DB_USER, "--auth=trust", "--encoding=UTF8", "--locale=C")
    running = pg_command(binaries["pg_ctl"], "-D", CLUSTER, "status", check=False) == 0
    if running:
        if not STATE.is_file() or json.loads(STATE.read_text())["port"] != port:
            raise RuntimeError("Local preview database is already running on another port; use --stop-db first")
    else:
        pg_command(binaries["pg_ctl"], "-D", CLUSTER, "-l", LOCAL / "postgres.log",
                   "-o", f"-h 127.0.0.1 -p {port}", "-w", "start")
        STATE.write_text(json.dumps({"port": port}), encoding="utf-8")
    with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user=DB_USER,
                         connect_timeout=5, autocommit=True) as connection:
        # Verify ownership before creating anything, even if a port was reused.
        data_directory = connection.execute("SHOW data_directory").fetchone()[0]
        if Path(data_directory).resolve() != CLUSTER.resolve():
            raise RuntimeError("The port belongs to a different PostgreSQL cluster")
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))


def local_environment(db_port, api_port):
    environment = os.environ.copy()
    environment.update({
        "DATABASE_URL": f"postgresql://{DB_USER}@127.0.0.1:{db_port}/{DATABASE}",
        "CLIENT_CONFIG_SOURCE": "database", "API_HOST": "127.0.0.1", "API_PORT": str(api_port),
        "API_RELOAD": "false", "SESSION_COOKIE_SECURE": "false",
    })
    environment.pop("PORT", None)
    environment.pop("RAILWAY_ENVIRONMENT", None)
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db-port", type=int, default=55432)
    parser.add_argument("--prepare-only", action="store_true", help="Create the database and clients without starting HTTP")
    parser.add_argument("--stop-db", action="store_true", help="Stop only this project's local preview database")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1024 <= args.db_port <= 65535:
        parser.error("Ports must be between 1024 and 65535")
    try:
        if args.stop_db:
            if (CLUSTER / "postmaster.pid").is_file():
                pg_command(postgres_binaries()["pg_ctl"], "-D", CLUSTER, "-w", "stop", "-m", "fast")
            print("Local preview database stopped. Its data is preserved.")
            return 0
        prepare_database(args.db_port)
        environment = local_environment(args.db_port, args.port)
        for module in ("backend.persistence.migrate", "backend.clients.setup_local"):
            subprocess.run([sys.executable, "-m", module], cwd=ROOT, env=environment, check=True)
        if args.prepare_only:
            return 0
        if not (ROOT / "Frontend" / "dist" / "index.html").is_file():
            raise RuntimeError("Build the frontend first: npm.cmd --prefix Frontend run build")
        from dotenv import dotenv_values
        if not (os.getenv("OPENAI_API_KEY") or dotenv_values(ROOT / ".env").get("OPENAI_API_KEY")):
            print("No OPENAI_API_KEY found. Pages/config work; add your key to .env and restart for AI replies.", flush=True)
        print(f"Alona:   http://127.0.0.1:{args.port}/c/alona", flush=True)
        print(f"Michael: http://127.0.0.1:{args.port}/c/michael-lipkin", flush=True)
        print("Ctrl+C stops HTTP. Run with --stop-db when you also want to stop the local database.", flush=True)
        return subprocess.call([sys.executable, "run_api.py"], cwd=ROOT, env=environment)
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, subprocess.CalledProcessError, psycopg.Error) as error:
        # Never display connection strings or credentials from environment settings.
        print(str(error) if isinstance(error, RuntimeError) else f"Local setup failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
