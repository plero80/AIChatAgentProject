from contextlib import contextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


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
    ):
        self.pool = ConnectionPool(
            conninfo=(
                f"host={host} port={port} dbname={dbname} "
                f"user={user} password={password}"
            ),
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    @contextmanager
    def transaction(self):
        """Run several statements on one connection, committed together."""
        with self.pool.connection() as connection:
            with connection.transaction():
                yield _Transaction(connection)

    def execute(
        self,
        query: str,
        params: tuple | None = None,
    ) -> None:

        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)

    def fetch_one(
        self,
        query: str,
        params: tuple | None = None,
    ) -> dict[str, Any] | None:

        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)

                return cursor.fetchone()

    def fetch_all(
        self,
        query: str,
        params: tuple | None = None,
    ) -> list[dict[str, Any]]:

        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)

                return cursor.fetchall()

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
