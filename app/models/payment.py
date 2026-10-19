"""Plain transfer domain records, independent of HTTP and idempotency."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TransferResult:
    kind: Literal["accepted", "rejected"]
    body: dict[str, Any]


@dataclass(frozen=True)
class TransferPlan:
    payer_id: int
    payee_ids: dict[str, int]
    balance_deltas: dict[int, int]
