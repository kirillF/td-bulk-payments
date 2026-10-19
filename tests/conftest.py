from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from app.core.config import Settings
from app.db.migrate import migrate
from app.main import create_app

PAYER = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
PAYEE = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
OTHER = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[Any]:
    # Real session-owned PostgreSQL; Docker failure never becomes a silent skip.
    with PostgresContainer("postgres:17", driver=None) as postgres:
        yield postgres


@pytest.fixture(scope="session")
def database_url(postgres_container: Any) -> str:
    url = postgres_container.get_connection_url()
    migrate(url)
    return str(url)


@pytest.fixture
def seeded_database(database_url: str) -> str:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "TRUNCATE request_result_receipts,request_execution_guards,payments,"
            "bulk_payment_requests,firms RESTART IDENTITY"
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO firms (name, uuid, balance_cents) VALUES (%s, %s, %s)",
                [("Payer", PAYER, 200_000), ("Payee", PAYEE, 0), ("Other", OTHER, 0)],
            )
    return database_url


@pytest.fixture
def application(seeded_database: str) -> Iterator[FastAPI]:
    with running_app(seeded_database) as app:
        yield app


def payload(amount: str = "10", payee: str = PAYEE, payer: str = PAYER) -> dict[str, Any]:
    return {
        "payer_firm_uuid": payer,
        "payments": [{"amount": amount, "payee_firm_uuid": payee, "description": "Services"}],
    }


def post(
    app: FastAPI,
    body: Any,
    key: str | None = "test-key",
    *,
    raise_exceptions: bool = True,
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return app.state.test_client.post("/v1/bulk-payments", json=body, headers=headers)


def balances(url: str) -> dict[str, int]:
    with psycopg.connect(url) as connection:
        return dict(connection.execute("SELECT uuid, balance_cents FROM firms").fetchall())


def count(url: str, table: str) -> int:
    from psycopg import sql

    assert table in {"payments", "bulk_payment_requests"}
    with psycopg.connect(url) as connection:
        row = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        return row.fetchone()[0]


@contextmanager
def running_app(configuration: Settings | str) -> Iterator[FastAPI]:
    app = create_app(configuration)
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.test_client = client
        yield app


@contextmanager
def borrowed(app):
    """Synchronous test inspection on the app-owned loop, never in production."""
    portal = app.state.test_client.portal
    connection = portal.call(app.state.pool.getconn)

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            return portal.call(self.cursor.fetchone)

        def fetchall(self):
            return portal.call(self.cursor.fetchall)

    class Session:
        def __getattr__(self, name):
            return getattr(connection, name)

        def execute(self, query, params=None):
            return Cursor(portal.call(partial(connection.execute, query, params)))

        def close(self):
            return portal.call(connection.close)

    try:
        yield Session()
        portal.call(connection.commit)
    except BaseException:
        if not connection.closed:
            portal.call(connection.rollback)
        raise
    finally:
        portal.call(app.state.pool.putconn, connection)


def pytest_bdd_apply_tag(tag: str, function: Any) -> bool | None:
    if tag.startswith("SC-"):
        pytest.mark.scenario(tag)(function)
        return True
    return None


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--scenarios", default=None, help="Explicit comma-separated approved scenario IDs"
    )


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    value = config.getoption("--scenarios")
    if value is None:
        return
    selected = set(value.split(","))
    found = set()
    kept = []
    deselected = []
    for item in items:
        marker = item.get_closest_marker("scenario")
        if marker is None:
            kept.append(item)
        elif marker.args[0] in selected:
            kept.append(item)
            found.add(marker.args[0])
        else:
            deselected.append(item)
    if selected - found:
        raise pytest.UsageError("Missing scenario bindings: " + str(selected - found))
    items[:] = kept
    config.hook.pytest_deselected(items=deselected)
