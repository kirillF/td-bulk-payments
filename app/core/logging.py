"""Dedicated app-owned JSON logger with bounded, allowlisted producer snapshots."""

import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from app.core.request_context import CURRENT, RequestContext

FIELDS = {
    "correlation_id",
    "request_id",
    "trace_id",
    "span_id",
    "route",
    "method",
    "status_code",
    "duration_ms",
    "outcome",
    "error_code",
    "cleanup_error_code",
    "error_type",
    "sqlstate",
    "ready",
    "stage",
    "ordinal",
}
EVENTS = {
    "service.started",
    "service.stopped",
    "service.readiness",
    "telemetry.degraded",
    "http.request.arrived",
    "http.request.completed",
    "stage.started",
    "stage.completed",
    "stage.failed",
    "database.cleanup.failed",
}
STAGES = {
    "request.prepare",
    "idempotency.resolve",
    "idempotency.claim",
    "idempotency.lookup",
    "idempotency.admission",
    "idempotency.guard",
    "idempotency.receipt",
    "idempotency.publish",
    "idempotency.cleanup",
    "controller.execute",
    "pool.acquire",
    "payment.transaction",
    "firms.lock",
    "payments.plan",
    "payments.persist",
    "payments.balances",
    "payments.insert",
    "transaction.commit",
    "health.probe",
    "database.cleanup",
    "recovery.resolve",
}


def request_fields(context: RequestContext | None = None) -> dict[str, Any]:
    context = context or CURRENT.get()
    if context is None:
        return {}
    fields = {
        "correlation_id": context.correlation_id,
        "request_id": context.request_id,
        "route": context.route,
        "method": context.method,
    }
    if context.span is not None:
        span = context.span.get_span_context()
        fields.update(trace_id=f"{span.trace_id:032x}", span_id=f"{span.span_id:016x}")
    return fields


class _JSONHandler(logging.Handler):
    def __init__(self, enqueue: Callable[[str], None], dropped: Callable[[str], None]) -> None:
        super().__init__()
        self.enqueue, self.dropped = enqueue, dropped

    def emit(self, record: logging.LogRecord) -> None:
        try:
            fields = record.__dict__.get("safe_fields", {})
            self.enqueue(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "level": record.levelname,
                        "event": record.msg,
                        "service": "firm-payments",
                        "version": "0.1.0",
                        **fields,
                    },
                    ensure_ascii=True,
                )
            )
        except Exception:
            self.dropped("logs")


class StructuredLogger:
    def __init__(self, level: str, enqueue: Callable[[str], None], dropped: Callable[[str], None]):
        # Construct directly: no registry, root, server or third-party handler mutation.
        self.logger = logging.Logger("firm_payments", getattr(logging, level))
        self.logger.propagate = False
        self.logger.addHandler(_JSONHandler(enqueue, dropped))

    def event(self, event: str, **values: Any) -> None:
        level = values.pop("level", "INFO")
        severity = getattr(logging, level, logging.INFO)
        if not self.logger.isEnabledFor(severity) or event not in EVENTS:
            return
        safe = {key: value for key, value in values.items() if key in FIELDS}
        self.logger.log(severity, event, extra={"safe_fields": safe})


@contextmanager
def stage(name: str, *, ordinal: int | None = None) -> Iterator[None]:
    context = CURRENT.get()
    logger = context.logger if context else None
    fields = {**request_fields(context), "stage": name}
    if ordinal is not None and 1 <= ordinal <= 1000:
        fields["ordinal"] = ordinal
    enabled = logger is not None and name in STAGES
    if enabled and logger is not None:
        logger.event("stage.started", level="DEBUG", **fields)
    try:
        yield
    except BaseException:
        if enabled and logger is not None:
            logger.event("stage.failed", level="DEBUG", **fields)
        raise
    else:
        if enabled and logger is not None:
            logger.event("stage.completed", level="DEBUG", **fields)


def instrument[**P, T](
    name: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Coroutine[Any, Any, T]]]:
    def decorate(function: Callable[P, Awaitable[T]]) -> Callable[P, Coroutine[Any, Any, T]]:
        @wraps(function)
        async def observed(*args: P.args, **kwargs: P.kwargs) -> T:
            with stage(name):
                return await function(*args, **kwargs)

        return observed

    return decorate


def record_cleanup_failure() -> None:
    """Retain one safe owner diagnostic independently of the request outcome."""
    context = CURRENT.get()
    if context is None or context.cleanup_failed:
        return
    context.cleanup_failed = True
    if context.logger is not None:
        context.logger.event(
            "database.cleanup.failed",
            level="ERROR",
            **request_fields(context),
            cleanup_error_code="cleanup_failed",
        )
