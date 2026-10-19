"""Independent admission, fencing and result lifecycle decisions; never financial work."""

from typing import Literal, cast
from uuid import uuid4

from psycopg.errors import LockNotAvailable

from app.core.logging import instrument
from app.crud import idempotency as storage
from app.db.database import Connection, Row, savepoint
from app.models.idempotency import (
    Admission,
    AdmissionLost,
    Completed,
    Conflict,
    Decision,
    Processing,
    StoredResponse,
)
from app.schemas.idempotency import StoredResultInvalid, validate_result


def identity(row: Row) -> Admission:
    return Admission(
        row["payer_firm_uuid"],
        row["idempotency_key"],
        row["request_fingerprint"],
        row["generation"],
        row["owner_token"],
    )


def cached(row: Row) -> StoredResponse:
    return StoredResponse(
        cast(Literal[201, 422], row["status_code"]),
        validate_result(row["status_code"], row["response_body"]),
    )


@instrument("idempotency.lookup")
async def find(connection: Connection, payer: str, key: str) -> Row | None:
    return await storage.find(connection, payer, key)


@instrument("idempotency.claim")
async def claim(
    connection: Connection, payer: str, key: str, fingerprint: str, budget_ms: int
) -> Admission | None:
    admission = Admission(payer, key, fingerprint, uuid4(), uuid4())
    return admission if await storage.claim(connection, admission, budget_ms) else None


async def _locked_current(
    connection: Connection, admission: Admission, *, nowait: bool = False
) -> Row | None:
    """One guard acquisition and fresh ownership check, before any state decision."""
    guard = await storage.lock_guard(connection, admission, nowait=nowait)
    row = await find(connection, admission.payer_uuid, admission.key)
    if row is None or identity(row) != admission:
        return None
    if not guard:
        raise StoredResultInvalid("Request generation has no execution guard")
    return row


@instrument("idempotency.guard")
async def acquire_execution_guard(connection: Connection, admission: Admission) -> None:
    row = await _locked_current(connection, admission)
    if row is None or row["state"] != "processing" or row["abandoned"]:
        raise AdmissionLost("Execution admission is no longer current")


@instrument("idempotency.receipt")
async def store_receipt(
    connection: Connection, admission: Admission, response: StoredResponse
) -> None:
    body = validate_result(response.status_code, response.body)
    await storage.create_receipt(connection, admission, StoredResponse(response.status_code, body))


async def receipt(connection: Connection, admission: Admission) -> StoredResponse | None:
    row = await storage.find_receipt(connection, admission)
    if row is None:
        return None
    if row["owner_token"] != admission.owner or row["request_fingerprint"] != admission.fingerprint:
        raise StoredResultInvalid("Receipt ownership mismatch")
    return cached(row)


async def _validated_receipt(
    connection: Connection, admission: Admission, row: Row
) -> StoredResponse | None:
    result = await receipt(connection, admission)
    if row["state"] == "completed":
        if result is None or cached(row) != result:
            raise StoredResultInvalid("Completed generation has invalid receipt")
    elif row["state"] != "processing":
        raise StoredResultInvalid("Invalid lifecycle state")
    return result


@instrument("idempotency.publish")
async def _publish(connection: Connection, admission: Admission, result: StoredResponse) -> None:
    if await storage.complete(connection, admission, result) != 1:
        raise AdmissionLost("Publication admission is no longer current")


async def publish_completed(connection: Connection, admission: Admission) -> StoredResponse:
    row = await _locked_current(connection, admission)
    if row is None:
        raise AdmissionLost("Publication admission is no longer current")
    result = await _validated_receipt(connection, admission, row)
    if result is None:
        raise StoredResultInvalid("Publication requires a committed receipt")
    if row["state"] == "processing":
        await _publish(connection, admission, result)
    return result


@instrument("idempotency.cleanup")
async def release_failed(connection: Connection, admission: Admission) -> None:
    """Caller must have joined the rolled-back business operation first."""
    row = await _locked_current(connection, admission)
    if (
        row is not None
        and row["state"] == "processing"
        and await receipt(connection, admission) is None
    ):
        await storage.delete(connection, admission)


@instrument("recovery.resolve")
async def reconcile(connection: Connection, admission: Admission) -> str:
    """One nonblocking resolution; retain live owners and never replay monetary work."""
    try:
        async with savepoint(connection):
            row = await _locked_current(connection, admission, nowait=True)
            if row is None:
                return "skipped"
            result = await _validated_receipt(connection, admission, row)
            if row["state"] == "completed":
                if not row["expired"]:
                    return "skipped"
                await storage.delete(connection, admission)
                return "deleted"
            if result is not None:
                await _publish(connection, admission, result)
                return "completed"
            if row["abandoned"]:
                await storage.delete(connection, admission)
                return "released"
            return "skipped"
    except LockNotAvailable:
        return "skipped"


@instrument("idempotency.admission")
async def check(
    connection: Connection, payer: str, key: str, fingerprint: str, budget_ms: int
) -> Decision:
    admission = await claim(connection, payer, key, fingerprint, budget_ms)
    if admission is not None:
        return admission
    previous = await find(connection, payer, key)
    if previous is None:
        return Processing()
    # Expiry precedes fingerprint comparison: a reusable key can carry new content.
    if previous["expired"]:
        resolved = await reconcile(connection, identity(previous))
        if resolved in {"deleted", "released"}:
            return await claim(connection, payer, key, fingerprint, budget_ms) or Processing()
        if resolved != "completed":
            return Processing()
        previous = await find(connection, payer, key)
        if previous is None:
            return Processing()
    if previous["request_fingerprint"] != fingerprint:
        return Conflict()
    if previous["state"] == "completed":
        result = await _validated_receipt(connection, identity(previous), previous)
        assert result is not None  # Validated completed generations require a receipt.
        return Completed(result)
    if previous["state"] != "processing":
        raise StoredResultInvalid("Invalid lifecycle state")
    resolved = await reconcile(connection, identity(previous))
    if resolved == "completed":
        # The same transaction holds the generation guard until its response commits.
        row = await find(connection, payer, key)
        if row is None:
            raise AdmissionLost("Recovered generation disappeared")
        return Completed(cached(row))
    if resolved == "released":
        return await claim(connection, payer, key, fingerprint, budget_ms) or Processing()
    return Processing()
