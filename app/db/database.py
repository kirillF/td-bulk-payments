"""One bounded pool per process; checkout health is checked once by its caller."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import AsyncConnection, IsolationLevel
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from psycopg_pool.pool_async import ReturnConnection

from app.core.config import Settings
from app.core.logging import instrument
from app.core.observability import CURRENT, attempt_result, phase

OPERATION_END: ContextVar[float | None] = ContextVar("payment_operation_end", default=None)


@asynccontextmanager
async def operation_scope(pool: Pool) -> AsyncIterator[None]:
    """One absolute budget shared by every sequential checkout in a request."""
    end = asyncio.get_running_loop().time() + pool.settings.operation_budget_ms / 1000
    token = OPERATION_END.set(end)
    try:
        async with asyncio.timeout_at(end):
            yield
    finally:
        OPERATION_END.reset(token)


@dataclass
class Operation:
    connection: AsyncConnection[DictRow] | None = None
    committing: bool = False
    returning: bool = False


class Pool(AsyncConnectionPool[AsyncConnection[DictRow]]):
    settings: Settings
    active_operations: set[asyncio.Task[Any]]

    async def getconn(self, timeout: float | None = None) -> AsyncConnection[DictRow]:
        try:
            return await super().getconn(timeout=timeout)
        except BaseException:
            # psycopg-pool 3.3.3 retains cancelled/expired queue positions until
            # a connection returns. Prune only terminal errors under its queue
            # lock so cancelled callers do not consume bounded waiter capacity.
            # This protected-API adapter is deliberately tied to the locked
            # version and covered by real queued cancellation/recovery checks.
            async with self._lock:
                self._waiting = deque(
                    position for position in self._waiting if position.error is None
                )
            raise

    async def return_owned(self, connection: AsyncConnection[DictRow], owner: Operation) -> None:
        """Transfer ownership to pool maintenance without a cancellable handoff gap.

        Psycopg-pool3.3.3 already uses ReturnConnection for configured reset callbacks.
        Queueing is synchronous: even if its pool lock is contended, the maintenance
        worker owns disposal/replacement and capacity accounting. No monetary work
        runs in that worker. Keep this version-bound adapter covered by real races.
        """
        self._check_pool_putconn(connection)
        if self.closed:
            await connection.close()
        else:
            self.run_task(ReturnConnection(self, connection, from_getconn=False))
        owner.returning = True
        owner.connection = None


async def run_owned[T](
    pool: Pool,
    work: Callable[[AsyncConnection[DictRow]], Awaitable[T]],
    budget_ms: int,
    reserve_ms: int,
    operation: Operation | None = None,
) -> T:
    """One supervised child; discard its session before injecting cancellation."""
    owner = operation or Operation()
    loop = asyncio.get_running_loop()
    end = min(loop.time() + budget_ms / 1000, OPERATION_END.get() or float("inf"))
    work_deadline = end - min(reserve_ms, budget_ms / 2) / 1000

    async def execute() -> T:
        with phase("pool.acquire"):
            connection = await pool.getconn(
                timeout=min(
                    pool.settings.acquire_timeout_ms / 1000,
                    max(0.001, work_deadline - loop.time()),
                )
            )
        owner.connection = connection  # No await between checkout and ownership registration.
        try:
            try:
                await Pool.check_connection(connection)
            except BaseException:
                await connection.close()
                raise
            return await work(connection)
        finally:
            if (
                not connection.closed
                and connection.info.transaction_status != TransactionStatus.IDLE
            ):
                await connection.close()
            await pool.return_owned(connection, owner)

    task = asyncio.create_task(execute(), name="payment-db-operation")
    pool.active_operations.add(task)
    try:
        async with asyncio.timeout_at(work_deadline):
            return await asyncio.shield(task)
    except BaseException as original_error:

        @instrument("database.cleanup")
        async def cleanup() -> BaseException | None:
            try:
                # close_returns is false: disposal does not perform rollback I/O.
                if not owner.returning:
                    try:
                        if owner.connection is not None:
                            await owner.connection.close()
                    finally:
                        task.cancel()
                async with asyncio.timeout_at(end):
                    await task
            except asyncio.CancelledError:
                return None
            except BaseException as cleanup_error:
                return cleanup_error
            return None

        cleanup_task = asyncio.create_task(cleanup(), name="payment-db-cleanup")
        # Repeated caller cancellation must not orphan cleanup or its borrowed session.
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        cleanup_error = cleanup_task.result()
        if cleanup_error is not None:
            attempt_result(
                "invariant_breach",
                error=cleanup_error
                if isinstance(cleanup_error, Exception)
                else RuntimeError("cleanup failed"),
            )
        if isinstance(original_error, asyncio.CancelledError):
            # Cleanup timeout must never manufacture an HTTP result for a cancelled caller.
            raise original_error
        if cleanup_error is not None:
            raise cleanup_error from original_error
        raise original_error
    finally:
        pool.active_operations.discard(task)


def create_pool(configuration: Settings | str) -> Pool:
    settings = Settings.local(configuration) if isinstance(configuration, str) else configuration

    async def configure(connection: AsyncConnection[DictRow]) -> None:
        await connection.set_isolation_level(IsolationLevel.READ_COMMITTED)

    options = " ".join(
        f"-c {name}={value}"
        for name, value in {
            "statement_timeout": settings.statement_timeout_ms,
            "lock_timeout": settings.lock_timeout_ms,
            "idle_in_transaction_session_timeout": settings.idle_transaction_timeout_ms,
            "transaction_timeout": settings.transaction_timeout_ms,
        }.items()
    )
    pool = Pool(
        settings.database_url,
        connection_class=AsyncConnection[DictRow],
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": settings.connect_timeout_seconds,
            "application_name": "firm-payments",
            "options": options,
        },
        min_size=settings.pool_min,
        max_size=settings.pool_max,
        max_waiting=settings.pool_max_waiting,
        timeout=settings.acquire_timeout_ms / 1000,
        reconnect_timeout=settings.connect_timeout_seconds,
        open=False,
        configure=configure,
    )
    pool.active_operations = set()
    pool.settings = settings
    return pool


async def validate_revision(connection: AsyncConnection[DictRow]) -> None:
    from alembic.script import ScriptDirectory

    from app.db.migrate import migration_config

    expected = ScriptDirectory.from_config(migration_config()).get_current_head()
    if expected is None:
        raise ValueError("Packaged migration head is missing")
    present = await (
        await connection.execute("SELECT to_regclass('alembic_version') IS NOT NULL AS valid")
    ).fetchone()
    if not present or not present["valid"]:
        raise ValueError("Unsupported database revision; run migrations before startup")
    rows = await (await connection.execute("SELECT version_num FROM alembic_version")).fetchall()
    if rows != [{"version_num": expected}]:
        raise ValueError("Unsupported database revision; run migrations before startup")


def validate_schema_sync(connection: psycopg.Connection[Any], *, lifecycle: bool = False) -> None:
    """Compatibility entry point for immutable revisions adopting the legacy schema."""
    from migrations.legacy_schema import validate_legacy_schema

    if lifecycle:
        raise ValueError("Current schema integrity is owned by Alembic migrations")
    validate_legacy_schema(connection)


class CommitUncertain(psycopg.OperationalError):
    pass


@asynccontextmanager
async def transaction_boundary(
    connection: AsyncConnection[DictRow], operation: Operation
) -> AsyncIterator[None]:
    transaction = connection.transaction()
    await transaction.__aenter__()
    try:
        yield
    except BaseException as error:
        await transaction.__aexit__(type(error), error, error.__traceback__)
        raise
    else:
        context = CURRENT.get()
        timing = (
            phase("transaction.commit") if context and context.commit_candidate else nullcontext()
        )
        try:
            operation.committing = True
            with timing:
                await transaction.__aexit__(None, None, None)
            operation.committing = False
        except psycopg.OperationalError as error:
            raise CommitUncertain("commit acknowledgement unavailable") from error


class OperationDeadlineExceeded(TimeoutError):
    pass


async def run_transaction[T](
    pool: Pool, work: Callable[[AsyncConnection[DictRow]], Awaitable[T]]
) -> T:
    operation = Operation()

    async def execute(connection: AsyncConnection[DictRow]) -> T:
        with phase("payment.transaction"):
            async with transaction_boundary(connection, operation):
                return await work(connection)

    try:
        return await run_owned(
            pool,
            execute,
            pool.settings.operation_budget_ms,
            pool.settings.response_margin_ms,
            operation,
        )
    except TimeoutError as error:
        if operation.committing:
            raise CommitUncertain("commit acknowledgement unavailable") from error
        raise OperationDeadlineExceeded("operation budget exhausted") from error
    except asyncio.CancelledError:
        if operation.committing:
            attempt_result(
                "unknown_commit", error=CommitUncertain("caller cancelled during commit")
            )
        else:
            attempt_result("client_disconnected")
        raise
