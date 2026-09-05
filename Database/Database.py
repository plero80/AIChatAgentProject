from typing import Any

import psycopg
from psycopg.rows import dict_row


class Database:

    def __init__(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
    ):
        self.connection = psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            row_factory=dict_row,
        )

    def execute(
        self,
        query: str,
        params: tuple | None = None,
    ) -> None:

        with self.connection.cursor() as cursor:
            cursor.execute(query, params)

        self.connection.commit()

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

    def close(self) -> None:
        self.connection.close()