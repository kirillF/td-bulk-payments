"""Task-local request metadata shared by HTTP, services, storage and cleanup."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequestRoute:
    route: str = "unmatched"
    method: str = "OTHER"
    is_probe: bool = False


@dataclass
class RequestContext:
    request_id: str
    route: str
    method: str
    started: float
    correlation_id: str = ""
    logger: Any = None
    is_probe: bool = False
    span: Any = None
    outcome: str = ""
    created: int = 0
    commit_candidate: bool = False
    cleanup_failed: bool = False
    error_type: str | None = None
    sqlstate: str | None = None
    error_code: str | None = None
    phases: list[tuple[str, int, int, float, str]] = field(default_factory=list)


CURRENT: ContextVar[RequestContext | None] = ContextVar("payment_request", default=None)
