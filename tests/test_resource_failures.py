"""Pool/SQL/transaction/operation exhaustion must clean up and permit same-key retry."""

import concurrent.futures
import importlib
import time

import psycopg
import pytest

from app.core.config import Settings

from .conftest import PAYER, balances, borrowed, count, payload, post, running_app


def test_pool_queue_and_acquisition_bounds(seeded_database):
    settings = Settings.local(
        seeded_database, pool_min=1, pool_max=1, pool_max_waiting=1, acquire_timeout_ms=150
    )
    with running_app(settings) as app:
        with borrowed(app):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(post, app, payload(), "waiting")
                deadline = time.monotonic() + 1
                while app.state.pool.get_stats()["requests_waiting"] != 1:
                    assert time.monotonic() < deadline
                    time.sleep(0.005)
                start = time.monotonic()
                rejected = post(app, payload(), "overload")
                assert rejected.status_code == 503
                assert future.result(timeout=2).status_code == 503
                assert time.monotonic() - start < 2
        assert count(seeded_database, "payments") == 0
        assert post(app, payload(), "waiting").status_code == 201
        assert app.state.pool.get_stats()["requests_waiting"] == 0


def test_statement_timeout_rollback_and_recovery(seeded_database):
    settings = Settings.local(
        seeded_database,
        lock_timeout_ms=50,
        statement_timeout_ms=100,
        idle_transaction_timeout_ms=200,
        transaction_timeout_ms=500,
    )
    with running_app(settings) as app:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "CREATE FUNCTION slow_payment() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN PERFORM pg_sleep(1); RETURN NEW; END $$; "
                "CREATE TRIGGER slow_payment BEFORE INSERT ON payments "
                "FOR EACH ROW EXECUTE FUNCTION slow_payment()"
            )
        try:
            assert post(app, payload()).status_code == 503
            assert count(seeded_database, "payments") == 0
            assert count(seeded_database, "bulk_payment_requests") == 0
            assert balances(seeded_database)[PAYER] == 200000
        finally:
            with psycopg.connect(seeded_database) as connection:
                connection.execute(
                    "DROP TRIGGER slow_payment ON payments; DROP FUNCTION slow_payment()"
                )
        assert post(app, payload()).status_code == 201


@pytest.mark.parametrize(
    ("setting", "wait"), [("idle_transaction_timeout_ms", 0.15), ("transaction_timeout_ms", 0.4)]
)
def test_server_transaction_bounds_disconnect_and_recover(seeded_database, setting, wait):
    values = {
        "lock_timeout_ms": 30,
        "statement_timeout_ms": 80,
        "idle_transaction_timeout_ms": 100,
        "transaction_timeout_ms": 300,
    }
    settings = Settings.local(seeded_database, **values)
    with running_app(settings) as app:
        with pytest.raises(psycopg.Error), borrowed(app) as connection:
            connection.execute("SELECT 1")
            if setting == "transaction_timeout_ms":
                # Keep issuing short statements so this exercises the whole-transaction bound.
                end = time.monotonic() + wait
                while time.monotonic() < end:
                    connection.execute("SELECT 1")
                    time.sleep(0.02)
            else:
                time.sleep(wait)
            connection.execute("SELECT 1")
        assert post(app, payload()).status_code == 201


def test_monotonic_expiry_rolls_back_balance_updates(application, seeded_database, monkeypatch):
    module = importlib.import_module("app.db.database")
    original = psycopg.AsyncConnection.execute
    checks = 0

    async def expire(connection, query, *args, **kwargs):
        nonlocal checks
        result = await original(connection, query, *args, **kwargs)
        if isinstance(query, str) and query.lstrip().startswith("UPDATE firms SET"):
            checks += 1
            raise module.OperationDeadlineExceeded("synthetic budget expiry after balance writes")
        return result

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", expire)
    assert post(application, payload()).status_code == 503
    assert checks == 1
    assert (
        count(seeded_database, "payments") == count(seeded_database, "bulk_payment_requests") == 0
    )
    assert balances(seeded_database)[PAYER] == 200000
    monkeypatch.setattr(psycopg.AsyncConnection, "execute", original)
    assert post(application, payload()).status_code == 201


def test_checkout_health_failure_is_not_retried(application, seeded_database, monkeypatch):
    from app.db.database import Pool

    calls = 0
    original = Pool.check_connection

    def fail(connection):
        nonlocal calls
        calls += 1
        raise psycopg.OperationalError("synthetic stale connection")

    monkeypatch.setattr(Pool, "check_connection", staticmethod(fail))
    assert post(application, payload()).status_code == 503
    assert calls == 1
    assert count(seeded_database, "bulk_payment_requests") == 0
    monkeypatch.setattr(Pool, "check_connection", staticmethod(original))
    assert post(application, payload()).status_code == 201


def test_startup_and_connection_configuration_are_bounded(seeded_database):
    with running_app(Settings.local(seeded_database)) as app:
        with borrowed(app) as connection:
            assert connection.info.get_parameters()["connect_timeout"] == "2"
            for setting, value in [
                ("statement_timeout", "3s"),
                ("lock_timeout", "2s"),
                ("idle_in_transaction_session_timeout", "5s"),
                ("transaction_timeout", "10s"),
            ]:
                assert connection.execute("SHOW " + setting).fetchone()[setting] == value
    settings = Settings.local(
        "postgresql://fixture@127.0.0.1:1/fixture",
        connect_timeout_seconds=1,
        startup_timeout_ms=100,
    )
    start = time.monotonic()
    with pytest.raises(Exception, match="pool initialization incomplete|pool"):
        with running_app(settings):
            pass
    assert time.monotonic() - start < 3


class ReplyBlackhole:
    """Forward real PostgreSQL traffic, then withhold replies while TCP stays open."""

    def __init__(self, database_url):
        import socket
        import threading

        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        self.socket_module = socket
        self.threading = threading
        info = conninfo_to_dict(database_url)
        self.upstream = (info["host"], int(info["port"]))
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.url = make_conninfo(
            database_url, host="127.0.0.1", port=self.listener.getsockname()[1], sslmode="disable"
        )
        self.stop = threading.Event()
        self.withheld = threading.Event()
        self.target = None
        self.sockets = []
        self.threads = []
        self.acceptor = threading.Thread(target=self.accept, daemon=True)

    def __enter__(self):
        self.acceptor.start()
        return self

    def accept(self):
        while not self.stop.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            server = self.socket_module.create_connection(self.upstream, timeout=1)
            client.settimeout(0.1)
            server.settimeout(0.1)
            self.sockets += [client, server]
            done, stalled = self.threading.Event(), self.threading.Event()
            for source, destination, frontend in [(client, server, True), (server, client, False)]:
                thread = self.threading.Thread(
                    target=self.pump,
                    args=(source, destination, frontend, done, stalled),
                    daemon=True,
                )
                self.threads.append(thread)
                thread.start()

    def pump(self, source, destination, frontend, done, stalled):
        buffer = b""
        previous_target = None
        try:
            while not self.stop.is_set() and not done.is_set():
                try:
                    data = source.recv(65536)
                except TimeoutError:
                    continue
                if not data:
                    break
                if frontend:
                    if self.target is not None and self.target != previous_target:
                        buffer = b""
                        previous_target = self.target
                    buffer = (buffer + data.lower())[-8192:]
                    if self.target and self.target in buffer:
                        self.target = None  # Fail only this operation; recovery stays reachable.
                        stalled.set()
                elif stalled.is_set():
                    self.withheld.set()  # Actual server bytes exist but are not delivered.
                    while not self.stop.is_set() and not done.wait(0.01):
                        pass
                    break
                destination.sendall(data)
        except OSError:
            pass
        finally:
            done.set()
            for sock in (source, destination):
                try:
                    sock.shutdown(self.socket_module.SHUT_RDWR)
                except OSError:
                    pass

    def __exit__(self, *args):
        self.stop.set()
        self.acceptor.join(2)
        self.listener.close()
        for sock in self.sockets:
            sock.close()
        for thread in self.threads:
            thread.join(1)
        assert not self.acceptor.is_alive() and not any(t.is_alive() for t in self.threads)


@pytest.mark.parametrize("phase", ["checkout", "payment", "commit", "rollback"])
def test_stalled_tcp_replies_expire_discard_and_recover(seeded_database, phase):
    with ReplyBlackhole(seeded_database) as fault:
        settings = Settings.local(
            fault.url,
            pool_min=1,
            pool_max=1,
            acquire_timeout_ms=100,
            lock_timeout_ms=100,
            statement_timeout_ms=1000,
            idle_transaction_timeout_ms=1500,
            transaction_timeout_ms=2000,
            operation_budget_ms=3400,
            response_margin_ms=300,
        )
        with running_app(settings) as app:
            logs = []
            app.state.telemetry.sink = logs.append
            trigger = phase == "rollback"
            if trigger:
                with psycopg.connect(seeded_database) as connection:
                    connection.execute(
                        "CREATE FUNCTION fail_transport_payment() RETURNS trigger LANGUAGE plpgsql "
                        "AS $$ BEGIN RAISE EXCEPTION 'fault'; END $$; "
                        "CREATE TRIGGER fail_transport_payment BEFORE INSERT ON payments "
                        "FOR EACH ROW EXECUTE FUNCTION fail_transport_payment()"
                    )
            with borrowed(app) as original:
                pass
            fault.target = {
                "checkout": b"q\x00\x00\x00\x05\x00",  # Pool check sends an empty query.
                "payment": b"insert into payments",
                "commit": b"commit\x00",  # Must not match BEGIN ... READ COMMITTED.
                "rollback": b"rollback\x00",
            }[phase]
            try:
                start = time.monotonic()
                result = post(app, payload("1"), "transport")
                assert result.status_code == 503 and result.headers["retry-after"] == "1"
                assert time.monotonic() - start < settings.operation_budget_ms / 1000 + 0.3
                assert fault.withheld.is_set(), (
                    "Must observe actual server replies withheld on open TCP"
                )
                assert original.closed
                if phase == "commit":
                    from .test_observability import wait_events

                    assert wait_events(logs, 1)[0]["outcome"] == "unknown_commit"
                    assert count(seeded_database, "payments") == 1
                else:
                    assert count(seeded_database, "payments") == 0
            finally:
                if trigger:
                    with psycopg.connect(seeded_database) as connection:
                        connection.execute(
                            "DROP TRIGGER fail_transport_payment ON payments; "
                            "DROP FUNCTION fail_transport_payment()"
                        )
            replay = post(app, payload("1"), "transport")
            assert replay.status_code == 201
            assert post(app, payload("1"), "transport").json() == replay.json()
            assert (
                count(seeded_database, "payments")
                == count(seeded_database, "bulk_payment_requests")
                == 1
            )
            assert balances(seeded_database)[PAYER] == 199900


@pytest.mark.parametrize("phase", ["queued", "payment", "commit"])
def test_external_cancellation_joins_operation_and_preserves_recovery(seeded_database, phase):
    import asyncio
    from contextlib import nullcontext

    from app.db.database import run_transaction
    from app.schemas.payments import BulkPayment
    from app.services.idempotency import execute

    async def pay_bulk(pool, request, key):
        return await run_transaction(pool, lambda connection: execute(connection, request, key))

    with ReplyBlackhole(seeded_database) as fault:
        settings = Settings.local(
            fault.url,
            pool_min=1,
            pool_max=1,
            acquire_timeout_ms=300,
            lock_timeout_ms=100,
            statement_timeout_ms=1000,
            idle_transaction_timeout_ms=1500,
            transaction_timeout_ms=2000,
            operation_budget_ms=3600,
            response_margin_ms=300,
        )
        with running_app(settings) as app:
            fault.target = {
                "queued": None,
                "payment": b"insert into payments",
                "commit": b"commit\x00",
            }[phase]
            with borrowed(app) if phase == "queued" else nullcontext():

                async def cancel_operation():
                    task = asyncio.create_task(
                        pay_bulk(
                            app.state.pool, BulkPayment.model_validate(payload("1")), "cancelled"
                        )
                    )
                    async with asyncio.timeout(1):
                        while not (
                            app.state.pool.get_stats().get("requests_waiting", 0)
                            if phase == "queued"
                            else fault.withheld.is_set()
                        ):
                            await asyncio.sleep(0.002)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert not app.state.pool.active_operations
                    assert app.state.pool.get_stats().get("requests_waiting", 0) == 0
                    assert not any(
                        t.get_name().startswith("payment-db-") and not t.done()
                        for t in asyncio.all_tasks()
                    )

                app.state.test_client.portal.call(cancel_operation)
            assert count(seeded_database, "payments") == (1 if phase == "commit" else 0)
            first = post(app, payload("1"), "cancelled")
            assert first.status_code == 201
            assert post(app, payload("1"), "cancelled").json() == first.json()
            assert (
                count(seeded_database, "payments")
                == count(seeded_database, "bulk_payment_requests")
                == 1
            )


def test_timeout_during_pool_return_does_not_close_reassigned_connection(application, monkeypatch):
    import asyncio

    from app.db.database import run_owned

    async def exercise():
        pool = application.state.pool
        original = pool.return_owned
        returned = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def delayed(connection, owner):
            await original(connection, owner)
            seen.append(connection)
            returned.set()
            await release.wait()

        monkeypatch.setattr(pool, "return_owned", delayed)

        async def work(connection):
            return 1

        operation = asyncio.create_task(run_owned(pool, work, 400, 200))
        await returned.wait()
        # The connection may already belong to another caller while putconn unwinds.
        reassigned = await pool.getconn()
        try:
            await asyncio.sleep(0.23)
            assert not reassigned.closed
            assert await (await reassigned.execute("SELECT 1")).fetchone() is not None
        finally:
            await reassigned.rollback()
            release.set()
            with pytest.raises(TimeoutError):
                await operation
            monkeypatch.setattr(pool, "return_owned", original)
            await pool.putconn(reassigned)
        assert not seen[0].closed
        assert not pool.active_operations

    application.state.test_client.portal.call(exercise)


def test_checkout_handoff_has_no_cancellable_pruning_gap(seeded_database, monkeypatch):
    import asyncio

    from psycopg_pool import AsyncConnectionPool

    from app.db.database import run_transaction
    from app.schemas.payments import BulkPayment
    from app.services.idempotency import execute

    async def pay_bulk(pool, request, key):
        return await run_transaction(pool, lambda connection: execute(connection, request, key))

    settings = Settings.local(seeded_database, pool_max=1)
    with running_app(settings) as app:

        async def exercise():
            pool = app.state.pool
            original = AsyncConnectionPool.getconn
            gate = asyncio.Event()
            checked_out = []

            async def handoff(self, *args, **kwargs):
                connection = await original(self, *args, **kwargs)
                await self._lock.acquire()
                checked_out.append(connection)
                gate.set()
                return connection

            monkeypatch.setattr(AsyncConnectionPool, "getconn", handoff)
            action = asyncio.create_task(
                pay_bulk(pool, BulkPayment.model_validate(payload("1")), "handoff")
            )
            try:
                await asyncio.wait_for(gate.wait(), 1)
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(action, 1)
                assert checked_out[0].closed
                assert not pool.active_operations
                assert pool.get_stats().get("requests_waiting", 0) == 0
            finally:
                pool._lock.release()
                monkeypatch.setattr(AsyncConnectionPool, "getconn", original)
            connection = await pool.getconn(timeout=1)
            await pool.putconn(connection)

        app.state.test_client.portal.call(exercise)
        assert post(app, payload("1"), "handoff").status_code == 201
        assert count(seeded_database, "payments") == 1


def test_external_cancellation_survives_cleanup_timeout_after_return(application, monkeypatch):
    import asyncio

    from app.db.database import run_owned

    async def exercise():
        pool = application.state.pool
        original = pool.return_owned
        returned = asyncio.Event()
        release = asyncio.Event()

        async def delayed(connection, owner):
            await original(connection, owner)
            returned.set()
            await release.wait()

        monkeypatch.setattr(pool, "return_owned", delayed)

        async def work(connection):
            return 1

        operation = asyncio.create_task(run_owned(pool, work, 300, 100))
        await returned.wait()
        reassigned = await pool.getconn()
        try:
            operation.cancel()
            start = asyncio.get_running_loop().time()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert asyncio.get_running_loop().time() - start < 0.5
            assert not reassigned.closed
            assert await (await reassigned.execute("SELECT 1")).fetchone() is not None
            assert not pool.active_operations
            assert not any(
                t.get_name().startswith("payment-db-") and not t.done() for t in asyncio.all_tasks()
            )
        finally:
            release.set()
            await reassigned.rollback()
            monkeypatch.setattr(pool, "return_owned", original)
            await pool.putconn(reassigned)

    application.state.test_client.portal.call(exercise)


@pytest.mark.parametrize("cancel_caller", [False, True])
def test_pre_handoff_pool_return_recovers_capacity(seeded_database, monkeypatch, cancel_caller):
    """Pool maintenance owns a queued return even while its actual handoff is blocked."""
    import asyncio

    from app.db.database import run_transaction
    from app.schemas.payments import BulkPayment
    from app.services.idempotency import execute

    settings = Settings.local(seeded_database, pool_max=1)
    with running_app(settings) as app:

        async def exercise():
            pool = app.state.pool
            original = pool.return_owned
            accepted = asyncio.Event()
            seen = []

            async def stalled_after_acceptance(connection, owner):
                # Gate the actual worker handoff before it can accept the pool lock.
                await pool._lock.acquire()
                await original(connection, owner)
                seen.append(connection)
                accepted.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(pool, "return_owned", stalled_after_acceptance)
            key = "pre-handoff-cancel" if cancel_caller else "pre-handoff-timeout"
            request = BulkPayment.model_validate(payload("1"))
            # Keep ordinary DB settings; shorten only this runner's operation budget.
            from app.db.database import Operation, run_owned, transaction_boundary

            owner = Operation()

            async def work(connection):
                async with transaction_boundary(connection, owner):
                    return await execute(connection, request, key)

            action = asyncio.create_task(run_owned(pool, work, 400, 200, owner))
            try:
                await asyncio.wait_for(accepted.wait(), 1)
                assert pool.get_stats()["pool_available"] == 0
                if cancel_caller:
                    action.cancel()
                with pytest.raises(asyncio.CancelledError if cancel_caller else TimeoutError):
                    await asyncio.wait_for(action, 1)
                assert not pool.active_operations
                assert not seen[0].closed
                assert not any(
                    t.get_name().startswith("payment-db-") and not t.done()
                    for t in asyncio.all_tasks()
                )
            finally:
                pool._lock.release()
                monkeypatch.setattr(pool, "return_owned", original)
            recovered = await pool.getconn(timeout=1)
            assert await (await recovered.execute("SELECT 1")).fetchone() is not None
            await recovered.rollback()
            await pool.putconn(recovered)
            result = await run_transaction(
                pool, lambda connection: execute(connection, request, key)
            )
            assert result.outcome == "replayed"

        app.state.test_client.portal.call(exercise)
        assert count(seeded_database, "payments") == 1
        assert count(seeded_database, "bulk_payment_requests") == 1
