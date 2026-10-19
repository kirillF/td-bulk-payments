"""Payment persistence primitives; caller owns policy and transaction boundaries."""

from collections.abc import Sequence

from app.db import database
from app.db.database import Connection, Row


async def lock_firms(connection: Connection, firm_uuids: Sequence[str]) -> dict[str, Row]:
    rows = await database.fetch_all(
        connection,
        """SELECT id, uuid, balance_cents FROM firms
           WHERE uuid = ANY(%s::text[]) ORDER BY id FOR NO KEY UPDATE""",
        (list(firm_uuids),),
    )
    return {row["uuid"]: row for row in rows}


async def apply_balances(connection: Connection, deltas: dict[int, int]) -> None:
    await database.execute(
        connection,
        """UPDATE firms SET balance_cents = firms.balance_cents + changes.delta
           FROM unnest(%s::integer[], %s::bigint[]) AS changes(id, delta)
           WHERE firms.id = changes.id""",
        (list(deltas), list(deltas.values())),
    )


async def create(
    connection: Connection, payer_id: int, payee_id: int, amount_cents: int, description: str
) -> int:
    row = await database.fetch_one(
        connection,
        """INSERT INTO payments
           (payer_firm_id, payee_firm_id, amount_cents, description)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (payer_id, payee_id, amount_cents, description),
    )
    if row is None:
        raise RuntimeError("payment insertion returned no ID")
    return int(row["id"])
