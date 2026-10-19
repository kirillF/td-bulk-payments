"""Lightweight request admission decisions; independent of payment execution."""

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.core.logging import instrument
from app.db import idempotency as storage
from app.models.idempotency import Completed, Conflict, Decision, Processing
from app.schemas.idempotency import StoredResultInvalid


@instrument("idempotency.admission")
async def check(
    connection: AsyncConnection[DictRow], payer: str, key: str, fingerprint: str, budget_ms: int
) -> Decision:
    admission = await storage.claim(connection, payer, key, fingerprint, budget_ms)
    if admission is not None:
        return admission
    previous = await storage.find(connection, payer, key)
    if previous is None:
        return Processing()  # A concurrent fenced deletion won; a later request may claim.
    if previous["expired"]:
        resolved = await storage.reconcile(connection, storage.identity(previous))
        if resolved in {"deleted", "released"}:
            return (
                await storage.claim(connection, payer, key, fingerprint, budget_ms) or Processing()
            )
        if resolved == "completed":
            previous = await storage.find(connection, payer, key)
            if previous is None:
                return Processing()
        else:
            return Processing()
    if previous["request_fingerprint"] != fingerprint:
        return Conflict()
    if previous["state"] == "completed":
        result = storage.cached(previous)
        if await storage.receipt(connection, storage.identity(previous)) != result:
            raise StoredResultInvalid("Cached result has no matching durable receipt")
        return Completed(result)
    if previous["state"] != "processing":
        raise StoredResultInvalid("Invalid lifecycle state")
    recovered = await storage.try_publish(connection, storage.identity(previous))
    if recovered is not None:
        return Completed(recovered)
    if previous["abandoned"] and await storage.try_release_abandoned(
        connection, storage.identity(previous)
    ):
        return await storage.claim(connection, payer, key, fingerprint, budget_ms) or Processing()
    return Processing()
