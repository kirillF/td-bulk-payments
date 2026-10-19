"""Bulk-transfer validation, balance changes and payment creation."""

from collections import defaultdict

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.core.observability import phase
from app.models.payment import TransferPlan, TransferResult
from app.schemas.payments import MAX_CENTS, BulkPayment, BulkPaymentResponse, PaymentCreated


async def lock_firms(
    connection: AsyncConnection[DictRow], request: BulkPayment
) -> dict[str, DictRow]:
    firm_uuids = sorted(
        {str(request.payer_firm_uuid), *(str(p.payee_firm_uuid) for p in request.payments)}
    )
    rows = await (
        await connection.execute(
            """SELECT id, uuid, balance_cents FROM firms
           WHERE uuid = ANY(%s::text[]) ORDER BY id FOR NO KEY UPDATE""",
            (firm_uuids,),
        )
    ).fetchall()
    return {row["uuid"]: row for row in rows}


def plan_transfer(request: BulkPayment, firms: dict[str, DictRow]) -> TransferPlan | TransferResult:
    """Validate locked balances and compute every delta before permitting writes."""
    payer_uuid = str(request.payer_firm_uuid)
    required = {payer_uuid, *(str(p.payee_firm_uuid) for p in request.payments)}
    if required != firms.keys():
        return TransferResult("rejected", {"detail": "unknown_firm"})
    payer = firms[payer_uuid]
    if payer["balance_cents"] < request.total_cents:
        return TransferResult("rejected", {"detail": "insufficient_funds"})
    deltas: dict[int, int] = defaultdict(int)
    deltas[payer["id"]] = -request.total_cents
    for payment in request.payments:
        deltas[firms[str(payment.payee_firm_uuid)]["id"]] += payment.amount_cents
    if any(row["balance_cents"] + deltas[row["id"]] > MAX_CENTS for row in firms.values()):
        return TransferResult("rejected", {"detail": "balance_limit_exceeded"})
    return TransferPlan(payer["id"], {uuid: row["id"] for uuid, row in firms.items()}, dict(deltas))


async def apply_balances(connection: AsyncConnection[DictRow], deltas: dict[int, int]) -> None:
    await connection.execute(
        """UPDATE firms SET balance_cents = firms.balance_cents + changes.delta
           FROM unnest(%s::integer[], %s::bigint[]) AS changes(id, delta)
           WHERE firms.id = changes.id""",
        (list(deltas), list(deltas.values())),
    )


async def insert_payments(
    connection: AsyncConnection[DictRow], request: BulkPayment, plan: TransferPlan
) -> BulkPaymentResponse:
    created = []
    for payment in request.payments:
        inserted = await (
            await connection.execute(
                """INSERT INTO payments (payer_firm_id, payee_firm_id, amount_cents, description)
               VALUES (%s, %s, %s, %s) RETURNING id""",
                (
                    plan.payer_id,
                    plan.payee_ids[str(payment.payee_firm_uuid)],
                    payment.amount_cents,
                    payment.description,
                ),
            )
        ).fetchone()
        if inserted is None:
            raise RuntimeError("payment insertion returned no ID")
        created.append(
            PaymentCreated(
                id=inserted["id"],
                payer_firm_uuid=request.payer_firm_uuid,
                payee_firm_uuid=payment.payee_firm_uuid,
                amount_cents=payment.amount_cents,
                description=payment.description,
            )
        )
    return BulkPaymentResponse(
        payments_created=len(created), total_amount_cents=request.total_cents, payments=created
    )


class BulkPaymentService:
    """Execute monetary work using an existing transaction-owned connection."""

    @staticmethod
    async def transfer(
        connection: AsyncConnection[DictRow], request: BulkPayment
    ) -> TransferResult:
        with phase("firms.lock"):
            firms = await lock_firms(connection, request)
        plan = plan_transfer(request, firms)
        if isinstance(plan, TransferResult):
            return plan
        # Every participant is already locked and every resulting balance validated.
        with phase("payments.persist"):
            await apply_balances(connection, plan.balance_deltas)
            response = await insert_payments(connection, request, plan)
        return TransferResult("accepted", response.model_dump(mode="json"))
