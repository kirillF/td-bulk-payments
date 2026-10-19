"""SC-24: operational probes do not mutate money or query DB for liveness/metrics."""

import asyncio
import time

import pytest
from psycopg_pool import PoolTimeout

from .conftest import balances, count


def get(app, path):
    return app.state.test_client.get(path)


def test_probe_isolation_and_failure_budget(application, seeded_database, monkeypatch):
    before = balances(seeded_database)
    calls = 0
    original = application.state.pool.getconn

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise PoolTimeout("CANARY database credentials")

    monkeypatch.setattr(application.state.pool, "getconn", fail)
    assert get(application, "/health/live").json() == {"status": "alive"}
    assert get(application, "/metrics").status_code == 200
    assert calls == 0
    start = time.monotonic()
    assert get(application, "/health/ready").status_code == 503
    assert time.monotonic() - start < 2
    assert calls == 1
    monkeypatch.setattr(application.state.pool, "getconn", original)
    assert get(application, "/health/ready").json() == {"status": "ready"}
    assert balances(seeded_database) == before
    assert (
        count(seeded_database, "payments") == count(seeded_database, "bulk_payment_requests") == 0
    )


def test_actual_database_outage_and_recovery():
    import socket

    from testcontainers.community.postgres import PostgresContainer

    from app.db.migrate import migrate

    from .conftest import running_app

    # This fault owns a separate database; an explicit host port survives Docker restart.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with PostgresContainer("postgres:17", driver=None).with_bind_ports(5432, port) as postgres:
        url = postgres.get_connection_url()
        migrate(url)
        with running_app(url) as app:
            assert get(app, "/health/ready").status_code == 200
            container = postgres.get_wrapped_container()
            container.stop(timeout=1)
            try:
                start = time.monotonic()
                assert get(app, "/health/live").status_code == 200
                assert get(app, "/metrics").status_code == 200
                assert get(app, "/health/ready").status_code == 503
                assert time.monotonic() - start < 3
            finally:
                container.start()
            deadline = time.monotonic() + 10
            while get(app, "/health/ready").status_code != 200:
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert count(url, "payments") == 0


def test_incompatible_schema_fails_initialization(seeded_database):
    import psycopg
    import pytest

    from .conftest import running_app

    with psycopg.connect(seeded_database) as connection:
        connection.execute("ALTER TABLE firms DROP CONSTRAINT firms_balance_cents_check")
    try:
        with pytest.raises(ValueError, match="Incompatible database constraints"):
            with running_app(seeded_database):
                pass
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "ALTER TABLE firms ADD CONSTRAINT firms_balance_cents_check "
                "CHECK (balance_cents>=0)"
            )


def test_incompatible_key_collation_rejected(seeded_database):
    import psycopg

    from .conftest import payload, post, running_app

    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "CREATE COLLATION test_key_ci (provider=icu, "
            "locale='und-u-ks-level2', deterministic=false)"
        )
        connection.execute(
            "ALTER TABLE bulk_payment_requests ALTER COLUMN "
            "idempotency_key TYPE text COLLATE test_key_ci"
        )
    try:
        with pytest.raises(ValueError, match="Incompatible idempotency key collation"):
            with running_app(seeded_database):
                pass
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "ALTER TABLE bulk_payment_requests ALTER COLUMN "
                'idempotency_key TYPE text COLLATE "C"'
            )
            connection.execute("DROP COLLATION test_key_ci")
    with running_app(seeded_database) as app:
        first = post(app, payload("1"), "K")
        second = post(app, payload("1"), "k")
        assert first.status_code == second.status_code == 201
        assert first.json()["payments"][0]["id"] != second.json()["payments"][0]["id"]
        assert count(seeded_database, "bulk_payment_requests") == 2


@pytest.mark.parametrize("stage", ["acquire", "begin", "checkout", "commit", "acquire_then_setup"])
def test_probe_deadline_covers_setup_and_cleanup(application, monkeypatch, stage):
    from contextlib import ExitStack

    import psycopg

    from app.db.database import Pool

    from .conftest import borrowed

    telemetry = application.state.telemetry
    telemetry.health_budget_ms = 100
    pool = application.state.pool
    if stage == "acquire":
        with ExitStack() as held:
            for _ in range(pool.settings.pool_max):
                held.enter_context(borrowed(application))
            start = time.monotonic()
            assert get(application, "/health/ready").status_code == 503
            assert time.monotonic() - start < telemetry.health_budget_ms / 1000 + 0.15
    else:
        touched = []
        if stage in {"checkout", "acquire_then_setup"}:
            original = Pool.check_connection

            async def delayed(connection):
                touched.append(connection)
                await original(connection)
                await connection.execute("SELECT pg_sleep(0.5)")

            monkeypatch.setattr(Pool, "check_connection", staticmethod(delayed))
        else:
            name = "__aenter__" if stage == "begin" else "__aexit__"
            original = getattr(psycopg.AsyncTransaction, name)

            async def delayed(self, *args):
                touched.append(self._conn)
                await self._conn.execute("SELECT pg_sleep(0.5)")
                return await original(self, *args)

            monkeypatch.setattr(psycopg.AsyncTransaction, name, delayed)
        if stage == "acquire_then_setup":
            acquire = pool.getconn

            async def delayed_acquire(*args, **kwargs):
                await asyncio.sleep(0.07)
                return await acquire(*args, **kwargs)

            monkeypatch.setattr(pool, "getconn", delayed_acquire)
        start = time.monotonic()
        assert get(application, "/health/ready").status_code == 503
        assert time.monotonic() - start < telemetry.health_budget_ms / 1000 + 0.15
        assert touched and touched[-1].closed
        monkeypatch.undo()
    assert get(application, "/health/ready").status_code == 200
    assert pool.get_stats().get("requests_waiting", 0) == 0
    assert not pool.active_operations
