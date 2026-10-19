"""Explicit bounded receipt reconciliation and safe request-generation retention."""

import argparse
import asyncio
import json
import math
from functools import partial

from app.core.config import Settings
from app.crud import idempotency as storage
from app.db.database import Pool, create_pool, operation_scope, run_transaction
from app.schemas.idempotency import StoredResultInvalid
from app.services import idempotency as service


async def maintain(pool: Pool, batch_size: int, max_batches: int) -> dict[str, int]:
    counts = dict.fromkeys(
        ["completed", "released", "deleted", "skipped", "unresolved", "examined", "batches"], 0
    )
    cursor: tuple[object, object] | None = None
    for _ in range(max_batches):
        rows = await run_transaction(
            pool, partial(storage.candidates, limit=batch_size, cursor=cursor)
        )
        counts["batches"] += 1
        for row in rows:
            admission = service.identity(row)
            try:
                result = await run_transaction(
                    pool, partial(service.reconcile, admission=admission)
                )
            except StoredResultInvalid:
                result = "unresolved"
            counts[result] += 1
            counts["examined"] += 1
            cursor = (row["expires_at"], row["generation"])
        if len(rows) < batch_size:
            break
    return counts


async def run(
    settings: Settings, batch_size: int, max_batches: int, timeout_seconds: float
) -> dict[str, int]:
    pool = create_pool(settings)
    try:
        # Reserve part of the explicit command allowance for pool shutdown.
        async with operation_scope(pool, budget_ms=max(1, int(timeout_seconds * 800))):
            await pool.open(wait=True, timeout=settings.startup_timeout_ms / 1000)
            return await maintain(pool, batch_size, max_batches)
    finally:
        async with asyncio.timeout(timeout_seconds * 0.2):
            await pool.close(timeout=timeout_seconds * 0.2)


def positive_integer(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return value


def positive_seconds(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("Must be finite and positive")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=positive_integer, required=True)
    parser.add_argument("--max-batches", type=positive_integer, required=True)
    parser.add_argument("--timeout-seconds", type=positive_seconds, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run(Settings.from_env(), args.batch_size, args.max_batches, args.timeout_seconds)
        )
    except Exception:
        print(json.dumps({"error": "maintenance_failed"}))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))
    if result["unresolved"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
