from contextlib import contextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from backend.tenancy import validate_dietitian_id


class Database:

    def __init__(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
        min_size: int = 1,
        max_size: int = 10,
        dietitian_id: str | None = None,
    ):
        self.dietitian_id = validate_dietitian_id(dietitian_id) if dietitian_id is not None else None
        self.pool = ConnectionPool(
            conninfo=make_conninfo(host=host, port=port, dbname=dbname, user=user, password=password),
            min_size=min_size,
            max_size=max_size,
            check=ConnectionPool.check_connection,
            kwargs={
                "row_factory": dict_row,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
            open=True,
        )

    @contextmanager
    def transaction(self):
        """Run several statements on one connection, committed together."""
        with self.pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('app.dietitian_id', %s, true)",
                    (self.dietitian_id or "",),
                )
                yield _Transaction(connection)

    def execute(
        self,
        query: str,
        params: tuple | None = None,
    ) -> None:

        with self.transaction() as tx:
            tx.execute(query, params)

    def fetch_one(
        self,
        query: str,
        params: tuple | None = None,
    ) -> dict[str, Any] | None:

        with self.transaction() as tx:
            return tx.fetch_one(query, params)

    def fetch_all(
        self,
        query: str,
        params: tuple | None = None,
    ) -> list[dict[str, Any]]:

        with self.transaction() as tx:
            return tx.fetch_all(query, params)

    def close(self) -> None:
        self.pool.close()


class _Transaction:
    """Same execute/fetch API as Database, pinned to one connection."""

    def __init__(self, connection):
        self.connection = connection

    def execute(
        self,
        query: str,
        params: tuple | None = None,
    ) -> None:

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)

    def fetch_one(
        self,
        query: str,
        params: tuple | None = None,
    ) -> dict[str, Any] | None:

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)

            return cursor.fetchone()

    def fetch_all(
        self,
        query: str,
        params: tuple | None = None,
    ) -> list[dict[str, Any]]:

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)

            return cursor.fetchall()
