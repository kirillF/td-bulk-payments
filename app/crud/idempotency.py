"""Request lifecycle SQL primitives; no recovery policy or transaction ownership."""

from psycopg.types.json import Jsonb

from app.db import database
from app.db.database import Connection, Row
from app.models.idempotency import Admission, StoredResponse


async def find(connection: Connection, payer: str, key: str) -> Row | None:
    return await database.fetch_one(
        connection,
        "SELECT *, expires_at <= clock_timestamp() AS expired, "
        "owner_deadline <= clock_timestamp() AS abandoned FROM bulk_payment_requests "
        "WHERE payer_firm_uuid=%s AND idempotency_key=%s",
        (payer, key),
    )


async def claim(connection: Connection, admission: Admission, budget_ms: int) -> bool:
    row = await database.fetch_one(
        connection,
        "INSERT INTO bulk_payment_requests "
        "(payer_firm_uuid,idempotency_key,request_fingerprint,state,generation,owner_token,"
        "owner_deadline,expires_at) VALUES (%s,%s,%s,'processing',%s,%s,"
        "now()+(%s * interval '1 millisecond'),now()+interval '24 hours') "
        "ON CONFLICT (payer_firm_uuid,idempotency_key) DO NOTHING RETURNING generation",
        (
            admission.payer_uuid,
            admission.key,
            admission.fingerprint,
            admission.generation,
            admission.owner,
            budget_ms,
        ),
    )
    if row is None:
        return False
    await database.execute(
        connection,
        "INSERT INTO request_execution_guards (generation,payer_firm_uuid,idempotency_key) "
        "VALUES (%s,%s,%s)",
        (admission.generation, admission.payer_uuid, admission.key),
    )
    return True


async def lock_guard(connection: Connection, admission: Admission, *, nowait: bool = False) -> bool:
    row = await database.fetch_one(
        connection,
        "SELECT generation FROM request_execution_guards WHERE generation=%s FOR UPDATE"
        + (" NOWAIT" if nowait else ""),
        (admission.generation,),
    )
    return row is not None


async def create_receipt(
    connection: Connection, admission: Admission, response: StoredResponse
) -> None:
    await database.execute(
        connection,
        "INSERT INTO request_result_receipts "
        "(generation,owner_token,request_fingerprint,status_code,response_body) "
        "VALUES (%s,%s,%s,%s,%s)",
        (
            admission.generation,
            admission.owner,
            admission.fingerprint,
            response.status_code,
            Jsonb(response.body),
        ),
    )


async def find_receipt(connection: Connection, admission: Admission) -> Row | None:
    return await database.fetch_one(
        connection,
        "SELECT * FROM request_result_receipts WHERE generation=%s",
        (admission.generation,),
    )


async def complete(connection: Connection, admission: Admission, response: StoredResponse) -> int:
    return await database.execute(
        connection,
        "UPDATE bulk_payment_requests SET state='completed',status_code=%s,response_body=%s,"
        "completed_at=statement_timestamp(),expires_at=statement_timestamp()+interval '24 hours' "
        "WHERE generation=%s AND owner_token=%s AND state='processing'",
        (response.status_code, Jsonb(response.body), admission.generation, admission.owner),
    )


async def delete(connection: Connection, admission: Admission) -> int:
    return await database.execute(
        connection,
        "DELETE FROM bulk_payment_requests WHERE generation=%s AND owner_token=%s",
        (admission.generation, admission.owner),
    )


async def candidates(
    connection: Connection, limit: int, cursor: tuple[object, object] | None
) -> list[Row]:
    where = " AND (r.expires_at,r.generation)>(%s,%s)" if cursor else ""
    return await database.fetch_all(
        connection,
        "SELECT r.* FROM bulk_payment_requests r WHERE "
        "((r.state='completed' AND r.expires_at<=clock_timestamp()) OR "
        "(r.state='processing' AND (r.owner_deadline<=clock_timestamp() OR EXISTS "
        "(SELECT 1 FROM request_result_receipts x WHERE x.generation=r.generation))))"
        + where
        + " ORDER BY r.expires_at,r.generation LIMIT %s",
        (*cursor, limit) if cursor else (limit,),
    )
