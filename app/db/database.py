"""One bounded pool per process; checkout health is checked once by its caller."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import AsyncConnection, IsolationLevel
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from psycopg_pool.pool_async import ReturnConnection

from app.core.config import Settings
from app.core.observability import CURRENT, attempt_result, phase


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
    end = loop.time() + budget_ms / 1000
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


async def validate_schema(connection: AsyncConnection[DictRow]) -> None:
    """Fail startup for incompatible imported schemas instead of trusting IF NOT EXISTS."""
    columns = {
        "firms": {
            "id": ("integer", True),
            "name": ("text", True),
            "balance_cents": ("integer", True),
            "uuid": ("text", True),
        },
        "payments": {
            "id": ("integer", True),
            "payer_firm_id": ("integer", True),
            "payee_firm_id": ("integer", True),
            "amount_cents": ("integer", True),
            "description": ("text", True),
        },
        "bulk_payment_requests": {
            "payer_firm_uuid": ("text", True),
            "idempotency_key": ("text", True),
            "request_fingerprint": ("text", True),
            "status_code": ("integer", False),
            "response_body": ("jsonb", False),
            "created_at": ("timestamp with time zone", True),
        },
    }
    constraints = {
        "firms": {
            "PRIMARY KEY (id)",
            "CHECK (balance_cents >= 0)",
            "CHECK (uuid::uuid::text = uuid)",
        },
        "payments": {
            "PRIMARY KEY (id)",
            "CHECK (amount_cents > 0)",
            "CHECK (payer_firm_id <> payee_firm_id)",
            "FOREIGN KEY (payer_firm_id) REFERENCES firms(id)",
            "FOREIGN KEY (payee_firm_id) REFERENCES firms(id)",
        },
        "bulk_payment_requests": {
            "PRIMARY KEY (payer_firm_uuid,idempotency_key)",
            "CHECK (length(idempotency_key) >= 1 AND length(idempotency_key) <= 128)",
            "CHECK (length(request_fingerprint) = 64)",
            "CHECK (status_code = ANY ARRAY[201,422])",
            "CHECK (status_code IS NULL = response_body IS NULL)",
        },
    }

    def normalized(value: str) -> str:
        return "".join(value.split()).replace("(", "").replace(")", "")

    for table, expected in columns.items():
        rows = await (
            await connection.execute(
                "SELECT attname,format_type(atttypid,atttypmod) AS type,attnotnull "
                "FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped",
                (table,),
            )
        ).fetchall()
        if {r["attname"]: (r["type"], r["attnotnull"]) for r in rows} != expected:
            raise ValueError("Incompatible database columns")
        checks = await (
            await connection.execute(
                "SELECT pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint WHERE conrelid=%s::regclass",
                (table,),
            )
        ).fetchall()
        if {normalized(r["definition"]) for r in checks} != {
            normalized(x) for x in constraints[table]
        }:
            raise ValueError("Incompatible database constraints: " + table)
    collation = await (
        await connection.execute(
            "SELECT attcollation='pg_catalog.\"C\"'::regcollation AS valid "
            "FROM pg_attribute WHERE attrelid='bulk_payment_requests'::regclass "
            "AND attname='idempotency_key' AND NOT attisdropped"
        )
    ).fetchone()
    if not collation or not collation["valid"]:
        raise ValueError("Incompatible idempotency key collation; explicit migration required")
    index = await (
        await connection.execute(
            "SELECT indisunique AND indisvalid AND indpred IS NULL AND indnkeyatts=1 "
            "AND indrelid='firms'::regclass AND indkey[0]=(SELECT attnum FROM pg_attribute "
            "WHERE attrelid='firms'::regclass AND attname='uuid') AS valid "
            "FROM pg_index WHERE indexrelid='firms_uuid_idx'::regclass"
        )
    ).fetchone()
    if not index or not index["valid"]:
        raise ValueError("Required unique firm UUID index missing")
    sequence = await (
        await connection.execute("SELECT pg_get_serial_sequence('payments','id') AS sequence")
    ).fetchone()
    if not sequence or not sequence["sequence"]:
        raise ValueError("Payment IDs require a generated sequence")


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
