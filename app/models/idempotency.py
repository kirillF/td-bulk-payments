"""Typed request admission decisions and durable result envelopes."""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True)
class Admission:
    payer_uuid: str
    key: str
    fingerprint: str
    generation: UUID
    owner: UUID


@dataclass(frozen=True)
class StoredResponse:
    status_code: Literal[201, 422]
    body: dict[str, Any]


@dataclass(frozen=True)
class Completed:
    response: StoredResponse


@dataclass(frozen=True)
class Processing:
    pass


@dataclass(frozen=True)
class Conflict:
    pass


type Decision = Admission | Completed | Processing | Conflict


class AdmissionLost(RuntimeError):
    """A stale or fenced owner cannot begin or publish financial work."""
