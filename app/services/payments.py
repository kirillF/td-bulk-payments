"""Bulk-transfer validation, balance changes and payment creation."""

from collections import defaultdict

from app.core.logging import stage
from app.core.observability import phase
from app.crud import payments as storage
from app.db.database import Connection, Row
from app.models.payment import TransferPlan, TransferResult
from app.schemas.payments import MAX_CENTS, BulkPayment, BulkPaymentResponse, PaymentCreated


def plan_transfer(request: BulkPayment, firms: dict[str, Row]) -> TransferPlan | TransferResult:
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


async def insert_payments(
    connection: Connection, request: BulkPayment, plan: TransferPlan
) -> BulkPaymentResponse:
    created = []
    for ordinal, payment in enumerate(request.payments, 1):
        with stage("payments.insert", ordinal=ordinal):
            payment_id = await storage.create(
                connection,
                plan.payer_id,
                plan.payee_ids[str(payment.payee_firm_uuid)],
                payment.amount_cents,
                payment.description,
            )
            created.append(
                PaymentCreated(
                    id=payment_id,
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
    async def transfer(connection: Connection, request: BulkPayment) -> TransferResult:
        with phase("firms.lock"):
            firm_uuids = sorted(
                {str(request.payer_firm_uuid), *(str(p.payee_firm_uuid) for p in request.payments)}
            )
            firms = await storage.lock_firms(connection, firm_uuids)
        with stage("payments.plan"):
            plan = plan_transfer(request, firms)
        if isinstance(plan, TransferResult):
            return plan
        # Every participant is already locked and every resulting balance validated.
        with phase("payments.persist"):
            with stage("payments.balances"):
                await storage.apply_balances(connection, plan.balance_deltas)
            response = await insert_payments(connection, request, plan)
        return TransferResult("accepted", response.model_dump(mode="json"))
