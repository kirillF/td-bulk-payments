"""Durable request reservation and replay within a caller-owned transaction."""

from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.core.observability import CURRENT, phase
from app.models.idempotency import Result
from app.schemas.payments import BulkPayment, BulkPaymentResponse
from app.services.payments import BulkPaymentService


class StoredResultInvalid(RuntimeError):
    """A committed key must never be executed again when its stored result is invalid."""


def replay_body(status: int, body: Any, request: BulkPayment) -> dict[str, Any]:
    try:
        if status == 201:
            response = BulkPaymentResponse.model_validate(body)
            if len(response.payments) != len(request.payments) or any(
                actual.payer_firm_uuid != request.payer_firm_uuid
                or actual.payee_firm_uuid != expected.payee_firm_uuid
                or actual.amount_cents != expected.amount_cents
                or actual.description != expected.description
                for actual, expected in zip(response.payments, request.payments, strict=True)
            ):
                raise StoredResultInvalid("stored success does not match request")
            return response.model_dump(mode="json")
        if (
            status == 422
            and isinstance(body, dict)
            and set(body) == {"detail"}
            and body["detail"] in {"unknown_firm", "insufficient_funds", "balance_limit_exceeded"}
        ):
            return body
    except (ValidationError, TypeError, ValueError) as error:
        raise StoredResultInvalid("stored result is invalid") from error
    raise StoredResultInvalid("stored result is incomplete")


async def execute(connection: AsyncConnection[DictRow], request: BulkPayment, key: str) -> Result:
    payer_uuid = str(request.payer_firm_uuid)
    fingerprint = request.fingerprint()
    with phase("idempotency.resolve"):
        inserted = await (
            await connection.execute(
                """INSERT INTO bulk_payment_requests
                   (payer_firm_uuid, idempotency_key, request_fingerprint)
               VALUES (%s, %s, %s)
               ON CONFLICT (payer_firm_uuid, idempotency_key) DO NOTHING
               RETURNING payer_firm_uuid""",
                (payer_uuid, key, fingerprint),
            )
        ).fetchone()

    if inserted is None:
        # ON CONFLICT waits for the competing transaction. READ COMMITTED
        # gives this next statement a fresh snapshot of its completed result.
        previous = await (
            await connection.execute(
                """SELECT request_fingerprint, status_code, response_body
               FROM bulk_payment_requests
               WHERE payer_firm_uuid = %s AND idempotency_key = %s""",
                (payer_uuid, key),
            )
        ).fetchone()
        if previous is None or previous["status_code"] is None:
            raise StoredResultInvalid("incomplete committed idempotency record")
        if previous["request_fingerprint"] != fingerprint:
            return Result("conflict", {"detail": "idempotency_key_conflict"}, "conflict")
        return Result(
            "accepted" if previous["status_code"] == 201 else "rejected",
            replay_body(previous["status_code"], previous["response_body"], request),
            "replayed",
        )

    result = await BulkPaymentService.transfer(connection, request)
    await connection.execute(
        """UPDATE bulk_payment_requests SET status_code = %s, response_body = %s
           WHERE payer_firm_uuid = %s AND idempotency_key = %s""",
        (201 if result.kind == "accepted" else 422, Jsonb(result.body), payer_uuid, key),
    )
    context = CURRENT.get()
    if context is not None and result.kind == "accepted":
        context.commit_candidate = True
    return Result(
        result.kind,
        result.body,
        "committed" if result.kind == "accepted" else "business_rejected",
    )
