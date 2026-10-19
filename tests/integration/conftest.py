"""Opt-in PostgreSQL tests: a disposable loopback cluster, never DATABASE_URL."""

import os
import shutil
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.persistence.database import Database


@pytest.fixture(scope="session")
def postgres_cluster(tmp_path_factory):
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Set RUN_POSTGRES_TESTS=1 to run an isolated PostgreSQL cluster")
    executable = shutil.which("initdb")
    if not executable:
        candidate = Path("C:/Program Files/PostgreSQL/16/bin/initdb.exe")
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        pytest.fail("PostgreSQL initdb/pg_ctl and pgvector are required for integration tests")
    binaries = Path(executable).parent
    pg_ctl = binaries / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
    directory = tmp_path_factory.mktemp("tenant-postgres").resolve()
    cluster = directory / "cluster"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def run(*args):
        # A Windows server descendant can inherit pipe handles and prevent
        # communicate() from reaching EOF after pg_ctl has already exited.
        log = directory / "commands.log"
        with log.open("a", encoding="utf-8") as output:
            result = subprocess.run(
                [str(arg) for arg in args], stdout=output, stderr=subprocess.STDOUT,
                timeout=45, creationflags=flags,
            )
        if result.returncode:
            pytest.fail(f"Disposable PostgreSQL command failed:\n{log.read_text(encoding='utf-8')[-3000:]}")
        return result

    run(executable, "-D", cluster, "-U", "tenant_test_admin", "--auth=trust", "--encoding=UTF8", "--locale=C")
    try:
        run(pg_ctl, "-D", cluster, "-l", directory / "server.log", "-o", f"-h 127.0.0.1 -p {port} -F", "-w", "start")
        yield {"host": "127.0.0.1", "port": port, "user": "tenant_test_admin", "password": ""}
    finally:
        # No production process or data directory is addressed by this fixture.
        assert cluster.resolve().is_relative_to(directory)
        if (cluster / "postmaster.pid").exists():
            run(pg_ctl, "-D", cluster, "-w", "stop", "-m", "fast")


@pytest.fixture
def isolated_database(postgres_cluster):
    name = "tenant_test_" + uuid4().hex
    with psycopg.connect(make_conninfo(**postgres_cluster, dbname="postgres"), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    databases = []

    def connect(dietitian_id=None, *, user=None):
        options = {**postgres_cluster, "dbname": name, "min_size": 1, "max_size": 2, "dietitian_id": dietitian_id}
        if user is not None:
            options["user"] = user
        db = Database(**options)
        databases.append(db)
        return db

    yield connect
    for db in reversed(databases):
        db.close()
    with psycopg.connect(make_conninfo(**postgres_cluster, dbname="postgres"), autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
