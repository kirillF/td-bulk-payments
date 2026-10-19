"""Durable operation result envelope."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Result:
    kind: Literal["accepted", "rejected", "conflict"]
    body: dict[str, Any]
    outcome: Literal["committed", "replayed", "business_rejected", "conflict"]
