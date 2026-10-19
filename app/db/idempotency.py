"""Request claims, execution fencing and durable result storage. No transaction ownership."""

from typing import Literal, cast
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.errors import LockNotAvailable
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.models.idempotency import Admission, AdmissionLost, StoredResponse
from app.schemas.idempotency import StoredResultInvalid, validate_result


async def find(connection: AsyncConnection[DictRow], payer: str, key: str) -> DictRow | None:
    return await (
        await connection.execute(
            "SELECT *, expires_at <= clock_timestamp() AS expired, "
            "owner_deadline <= clock_timestamp() AS abandoned FROM bulk_payment_requests "
            "WHERE payer_firm_uuid=%s AND idempotency_key=%s",
            (payer, key),
        )
    ).fetchone()


async def claim(
    connection: AsyncConnection[DictRow], payer: str, key: str, fingerprint: str, budget_ms: int
) -> Admission | None:
    admission = Admission(payer, key, fingerprint, uuid4(), uuid4())
    row = await (
        await connection.execute(
            "INSERT INTO bulk_payment_requests "
            "(payer_firm_uuid,idempotency_key,request_fingerprint,state,generation,owner_token,"
            "owner_deadline,expires_at) VALUES (%s,%s,%s,'processing',%s,%s,"
            "clock_timestamp()+(%s * interval '1 millisecond'),"
            "clock_timestamp()+interval '24 hours') "
            "ON CONFLICT (payer_firm_uuid,idempotency_key) DO NOTHING RETURNING generation",
            (payer, key, fingerprint, admission.generation, admission.owner, budget_ms),
        )
    ).fetchone()
    if row is None:
        return None
    await connection.execute(
        "INSERT INTO request_execution_guards (generation,payer_firm_uuid,idempotency_key) "
        "VALUES (%s,%s,%s)",
        (admission.generation, payer, key),
    )
    return admission


def identity(row: DictRow) -> Admission:
    return Admission(
        row["payer_firm_uuid"],
        row["idempotency_key"],
        row["request_fingerprint"],
        row["generation"],
        row["owner_token"],
    )


def cached(row: DictRow) -> StoredResponse:
    return StoredResponse(
        cast(Literal[201, 422], row["status_code"]),
        validate_result(row["status_code"], row["response_body"]),
    )


async def acquire_execution_guard(
    connection: AsyncConnection[DictRow], admission: Admission
) -> None:
    guard = await (
        await connection.execute(
            "SELECT generation FROM request_execution_guards WHERE generation=%s FOR UPDATE",
            (admission.generation,),
        )
    ).fetchone()
    row = await find(connection, admission.payer_uuid, admission.key)
    if (
        guard is None
        or row is None
        or identity(row) != admission
        or row["state"] != "processing"
        or row["abandoned"]
    ):
        raise AdmissionLost("Execution admission is no longer current")


async def store_receipt(
    connection: AsyncConnection[DictRow], admission: Admission, response: StoredResponse
) -> None:
    body = validate_result(response.status_code, response.body)
    await connection.execute(
        "INSERT INTO request_result_receipts "
        "(generation,owner_token,request_fingerprint,status_code,response_body) "
        "VALUES (%s,%s,%s,%s,%s)",
        (
            admission.generation,
            admission.owner,
            admission.fingerprint,
            response.status_code,
            Jsonb(body),
        ),
    )


async def receipt(
    connection: AsyncConnection[DictRow], admission: Admission
) -> StoredResponse | None:
    row = await (
        await connection.execute(
            "SELECT * FROM request_result_receipts WHERE generation=%s", (admission.generation,)
        )
    ).fetchone()
    if row is None:
        return None
    if row["owner_token"] != admission.owner or row["request_fingerprint"] != admission.fingerprint:
        raise StoredResultInvalid("Receipt ownership mismatch")
    return cached(row)


async def publish_completed(
    connection: AsyncConnection[DictRow], admission: Admission
) -> StoredResponse:
    guard = await (
        await connection.execute(
            "SELECT generation FROM request_execution_guards WHERE generation=%s FOR UPDATE",
            (admission.generation,),
        )
    ).fetchone()
    row = await find(connection, admission.payer_uuid, admission.key)
    if guard is None or row is None or identity(row) != admission:
        raise AdmissionLost("Publication admission is no longer current")
    result = await receipt(connection, admission)
    if result is None:
        raise StoredResultInvalid("Publication requires a committed receipt")
    if row["state"] == "completed":
        if cached(row) != result:
            raise StoredResultInvalid("Cached result differs from receipt")
        return result
    await connection.execute(
        "UPDATE bulk_payment_requests SET state='completed',status_code=%s,response_body=%s,"
        "completed_at=clock_timestamp(),expires_at=clock_timestamp()+interval '24 hours' "
        "WHERE generation=%s AND owner_token=%s AND state='processing'",
        (result.status_code, Jsonb(result.body), admission.generation, admission.owner),
    )
    return result


async def release_failed(connection: AsyncConnection[DictRow], admission: Admission) -> None:
    """Release only a joined, rolled-back attempt with no durable receipt."""
    guard = await (
        await connection.execute(
            "SELECT generation FROM request_execution_guards WHERE generation=%s FOR UPDATE",
            (admission.generation,),
        )
    ).fetchone()
    row = await find(connection, admission.payer_uuid, admission.key)
    if (
        guard is not None
        and row is not None
        and identity(row) == admission
        and row["state"] == "processing"
        and await receipt(connection, admission) is None
    ):
        await connection.execute(
            "DELETE FROM bulk_payment_requests WHERE generation=%s AND owner_token=%s",
            (admission.generation, admission.owner),
        )


async def try_publish(
    connection: AsyncConnection[DictRow], admission: Admission
) -> StoredResponse | None:
    if await receipt(connection, admission) is None:
        return None
    try:
        async with connection.transaction():
            await connection.execute(
                "SELECT generation FROM request_execution_guards "
                "WHERE generation=%s FOR UPDATE NOWAIT",
                (admission.generation,),
            )
            return await publish_completed(connection, admission)
    except LockNotAvailable:
        return None


async def try_release_abandoned(connection: AsyncConnection[DictRow], admission: Admission) -> bool:
    try:
        async with connection.transaction():
            await connection.execute(
                "SELECT generation FROM request_execution_guards "
                "WHERE generation=%s FOR UPDATE NOWAIT",
                (admission.generation,),
            )
            row = await find(connection, admission.payer_uuid, admission.key)
            if row is None or identity(row) != admission or not row["abandoned"]:
                return False
            if await receipt(connection, admission) is not None:
                return False
            await release_failed(connection, admission)
            return True
    except LockNotAvailable:
        return False
