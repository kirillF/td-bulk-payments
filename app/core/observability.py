"""App-owned, bounded and allowlisted telemetry; monetary code never exports signals."""

import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import StructuredLogger, request_fields, stage
from app.core.request_context import CURRENT as CURRENT
from app.core.request_context import RequestContext, RequestRoute
from app.schemas.headers import correlation_id

OUTCOMES = {
    "committed",
    "replayed",
    "business_rejected",
    "validation_rejected",
    "conflict",
    "processing",
    "observed",
    "transient_failure",
    "unknown_commit",
    "invariant_breach",
    "client_disconnected",
}
PHASES = {
    "pool.acquire",
    "payment.transaction",
    "idempotency.resolve",
    "firms.lock",
    "payments.persist",
    "transaction.commit",
}
ERROR_TYPES = {
    "PoolTimeout",
    "TooManyRequests",
    "OperationalError",
    "LockNotAvailable",
    "QueryCanceled",
    "StoredResultInvalid",
    "OperationDeadlineExceeded",
    "PoolClosed",
    "CommitUncertain",
    "IdleInTransactionSessionTimeout",
    "TransactionTimeout",
}


@contextmanager
def phase(name: str) -> Iterator[None]:
    start = time.time_ns()
    monotonic = time.monotonic()
    result = "success"
    try:
        with stage(name):
            yield
    except BaseException:
        result = "failure"
        raise
    finally:
        try:
            context = CURRENT.get()
            if context is not None and name in PHASES and len(context.phases) < 6:
                context.phases.append(
                    (name, start, time.time_ns(), time.monotonic() - monotonic, result)
                )
        except Exception:
            pass


def attempt_result(outcome: str, created: int = 0, error: Exception | None = None) -> None:
    context = CURRENT.get()
    if context is None:
        return
    context.outcome = outcome if outcome in OUTCOMES else "invariant_breach"
    context.created = created
    if error is not None:
        context.error_type = (
            type(error).__name__ if type(error).__name__ in ERROR_TYPES else "ApplicationError"
        )
        sqlstate = getattr(error, "sqlstate", None)
        context.sqlstate = (
            sqlstate
            if isinstance(sqlstate, str) and re.fullmatch("[0-9A-Z]{5}", sqlstate)
            else None
        )
        if context.error_type == "ApplicationError" and context.sqlstate is not None:
            context.error_type = "DatabaseError"
        context.error_code = (
            {
                "55P03": "lock_timeout",
                "57014": "statement_timeout",
                "25P03": "idle_timeout",
                "25P04": "transaction_timeout",
            }.get(context.sqlstate or "")
            or {
                "PoolTimeout": "pool_exhausted",
                "TooManyRequests": "pool_exhausted",
                "OperationDeadlineExceeded": "operation_timeout",
                "StoredResultInvalid": "invariant_breach",
                "CommitUncertain": "unknown_commit",
            }.get(context.error_type or "")
            or (
                "connection_failure"
                if context.error_type == "OperationalError"
                else "internal_error"
                if context.error_type == "ApplicationError"
                else "database_failure"
            )
        )


class SafeExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter, dropped: Callable[[str], None]) -> None:
        self.exporter = exporter
        self.dropped = dropped

    def export(self, spans: Any) -> SpanExportResult:
        try:
            result = self.exporter.export(spans)
            if result == SpanExportResult.SUCCESS:
                return result
        except Exception:
            pass
        self.dropped("traces")
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self.exporter.shutdown()
        except Exception:
            self.dropped("traces")


class BoundedSpanProcessor(SpanProcessor):
    """Public SDK processor interface with explicit, nonblocking overflow accounting."""

    def __init__(
        self,
        exporter: SpanExporter,
        dropped: Callable[[str], None],
        capacity: int,
        shutdown_seconds: float,
    ) -> None:
        self.exporter = SafeExporter(exporter, dropped)
        self.dropped = dropped
        self.queue: queue.Queue[ReadableSpan] = queue.Queue(capacity)
        self.stopping = threading.Event()
        self.shutdown_seconds = shutdown_seconds
        self.worker = threading.Thread(target=self._export, daemon=True, name="payment-traces")
        self.worker.start()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        if not span.context or not span.context.trace_flags.sampled:
            return
        if self.stopping.is_set():
            self.dropped("traces")
            return
        try:
            self.queue.put_nowait(span)
        except queue.Full:
            self.dropped("traces")

    def _export(self) -> None:
        try:
            while not self.stopping.is_set():
                try:
                    span = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    self.exporter.export((span,))
                finally:
                    self.queue.task_done()
        finally:
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
                self.dropped("traces")
                self.queue.task_done()
            self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        deadline = time.monotonic() + timeout_millis / 1000
        while self.queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.001)
        return True

    def shutdown(self) -> None:
        self.stopping.set()
        self.worker.join(self.shutdown_seconds)


class Telemetry:
    def __init__(
        self,
        settings: Settings,
        sink: Callable[[str], Any] | None = None,
        request_metadata: Callable[[Scope], RequestRoute] | None = None,
    ) -> None:
        self.request_metadata = request_metadata
        self.shutdown_seconds = settings.telemetry_shutdown_ms / 1000
        self.health_budget_ms = settings.health_probe_budget_ms
        self.export_timeout = settings.otlp_timeout_ms
        capacity = settings.log_queue_capacity
        ratio = settings.trace_sample_ratio
        self.registry = CollectorRegistry()
        self.attempts = Counter(
            "firm_payments_attempts_total",
            "Operational attempts",
            ["outcome"],
            registry=self.registry,
        )
        self.requests = Counter(
            "firm_payments_http_requests_total",
            "HTTP attempts",
            ["route", "method", "status_class"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "firm_payments_http_request_duration_seconds",
            "Attempt duration",
            ["route", "status_class"],
            registry=self.registry,
        )
        self.created = Counter(
            "firm_payments_created_total",
            "Acknowledged newly committed payments",
            registry=self.registry,
        )
        self.acquire = Histogram(
            "firm_payments_pool_acquire_duration_seconds",
            "Checkout duration",
            ["result"],
            registry=self.registry,
        )
        self.transactions = Histogram(
            "firm_payments_db_transaction_duration_seconds",
            "Transaction duration",
            ["result"],
            registry=self.registry,
        )
        self.failures = Counter(
            "firm_payments_db_failures_total", "DB failures", ["category"], registry=self.registry
        )
        self.connections = Gauge(
            "firm_payments_pool_connections", "Pool connections", registry=self.registry
        )
        self.waiters = Gauge("firm_payments_pool_waiters", "Pool waiters", registry=self.registry)
        self.drops = Counter(
            "firm_payments_telemetry_dropped_total",
            "Recording/export drops",
            ["signal"],
            registry=self.registry,
        )
        self.provider = TracerProvider(
            resource=Resource({"service.name": "firm-payments", "service.version": "0.1.0"}),
            shutdown_on_exit=False,
            sampler=TraceIdRatioBased(ratio),
        )
        self.tracer = self.provider.get_tracer("firm-payments")
        endpoint = settings.otlp_endpoint
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            self.provider.add_span_processor(
                BoundedSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint, timeout=self.export_timeout / 1000),
                    self.drop,
                    capacity=settings.trace_queue_capacity,
                    shutdown_seconds=self.shutdown_seconds,
                )
            )
        self.queue: queue.Queue[str] = queue.Queue(capacity)
        self.sink = sink or (lambda line: print(line, flush=True))
        self.stopping = threading.Event()
        self.degraded = False
        self.ready: bool | None = None
        self.worker = threading.Thread(target=self._write, daemon=True, name="payment-json-logs")
        self.worker.start()
        self.logger = StructuredLogger(settings.log_level, self.queue.put_nowait, self.drop)
        self.event("service.started")

    def drop(self, signal: str) -> None:
        self.degraded = True
        try:
            self.drops.labels(
                signal if signal in {"logs", "traces", "metrics"} else "metrics"
            ).inc()
        except Exception:
            pass

    def event(self, event: str, **values: Any) -> None:
        self.logger.event(event, **values)

    def _write(self) -> None:
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                line = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self.sink(line)
            except Exception:
                self.drop("logs")
            finally:
                self.queue.task_done()

    def begin(self, scope: Scope) -> RequestContext:
        route = RequestRoute()
        try:
            if self.request_metadata is not None:
                route = self.request_metadata(scope)
        except Exception:
            self.drop("metrics")
        context = RequestContext(
            uuid4().hex,
            route.route,
            route.method,
            time.monotonic(),
            correlation_id=correlation_id(scope.get("headers", [])),
            logger=self.logger,
            is_probe=route.is_probe,
        )
        try:
            carrier = {}
            for name, value in scope.get("headers", []):
                if name == b"traceparent" and len(value) == 55:
                    carrier["traceparent"] = value.decode("ascii", errors="ignore")
            parent = TraceContextTextMapPropagator().extract(carrier)
            context.span = self.tracer.start_span(
                "http.request", context=parent, attributes={"http.route": context.route}
            )
        except Exception:
            self.drop("traces")
        self.logger.event("http.request.arrived", **request_fields(context))
        return context

    def finish(self, context: RequestContext, status: int | None) -> None:
        outcome = context.outcome or (
            "observed"
            if status is not None and status < 400
            else "validation_rejected"
            if status is not None and status < 500
            else "transient_failure"
        )
        status_class = (
            str(status // 100) + "xx" if status is not None and 100 <= status < 600 else "none"
        )
        try:
            if not context.is_probe:
                self.requests.labels(context.route, context.method, status_class).inc()
                self.duration.labels(context.route, status_class).observe(
                    time.monotonic() - context.started
                )
                self.attempts.labels(outcome).inc()
                if context.created:
                    self.created.inc(context.created)
                if outcome in {"transient_failure", "unknown_commit", "invariant_breach"}:
                    self.failures.labels(context.error_code or outcome).inc()
                for name, _start, _end, duration, result in context.phases:
                    if name == "pool.acquire":
                        self.acquire.labels(result).observe(duration)
                    elif name == "payment.transaction":
                        self.transactions.labels(result).observe(duration)
        except Exception:
            self.drop("metrics")
        fields = {
            **request_fields(context),
            "request_id": context.request_id,
            "route": context.route,
            "method": context.method,
            "status_code": status,
            "duration_ms": round((time.monotonic() - context.started) * 1000, 3),
            "outcome": outcome,
        }
        try:
            if context.span is not None:
                span_context = context.span.get_span_context()
                fields.update(
                    trace_id=f"{span_context.trace_id:032x}", span_id=f"{span_context.span_id:016x}"
                )
                context.span.set_attribute("payment.outcome", outcome)
                if outcome in {"transient_failure", "unknown_commit", "invariant_breach"} or (
                    status is not None and status >= 500
                ):
                    context.span.set_status(trace.Status(trace.StatusCode.ERROR))
                if status is not None:
                    context.span.set_attribute("http.response.status_code", status)
                for name, start, end, _, result in context.phases:
                    child = self.tracer.start_span(
                        name,
                        context=trace.set_span_in_context(context.span),
                        start_time=start,
                        attributes={"phase.result": result},
                    )
                    child.end(end_time=end)
                context.span.end()
        except Exception:
            self.drop("traces")
        if context.error_code is not None:
            fields["error_code"] = context.error_code
        if context.error_type is not None:
            fields["error_type"] = context.error_type
        if context.sqlstate is not None:
            fields["sqlstate"] = context.sqlstate
        level = (
            "ERROR"
            if outcome == "invariant_breach"
            else "WARNING"
            if outcome in {"unknown_commit", "transient_failure"}
            or (status is not None and status >= 500)
            else "INFO"
        )
        self.event("http.request.completed", level=level, **fields)
        if self.degraded:
            self.degraded = False
            self.event("telemetry.degraded", level="WARNING")

    def readiness(self, ready: bool) -> None:
        if self.ready is not ready:
            self.ready = ready
            self.event("service.readiness", ready=ready, level="INFO" if ready else "WARNING")

    def metrics(self, pool: Any) -> bytes:
        try:
            stats = pool.get_stats()
            self.connections.set(stats.get("pool_size", 0))
            self.waiters.set(stats.get("requests_waiting", 0))
            return generate_latest(self.registry)
        except Exception:
            self.drop("metrics")
            return b""

    def close(self) -> None:
        self.event("service.stopped")
        self.stopping.set()
        self.worker.join(self.shutdown_seconds)
        thread = threading.Thread(target=self.provider.shutdown, daemon=True)
        thread.start()
        thread.join(self.shutdown_seconds)


class RequestTelemetryMiddleware:
    def __init__(self, app: ASGIApp, owner: Any) -> None:
        self.app = app
        self.owner = owner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        telemetry = self.owner.state.telemetry
        context = telemetry.begin(scope)
        token = CURRENT.set(context)
        status = None
        send_failed = False

        async def traced_send(message: Message) -> None:
            nonlocal status, send_failed
            try:
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() not in {b"x-request-id", b"x-correlation-id"}
                    ] + [
                        (b"x-request-id", context.request_id.encode()),
                        (b"x-correlation-id", context.correlation_id.encode()),
                    ]
                    await send(message)
                    status = message["status"]
                else:
                    await send(message)
            except OSError:
                send_failed = True
                context.outcome = "client_disconnected"
                raise

        try:
            await self.app(scope, receive, traced_send)
        except Exception as error:
            if context.error_type is None:
                attempt_result(context.outcome or "transient_failure", context.created, error)
            if status is None and not send_failed:
                # Generate the sanitized error while correlation is still in scope.
                # Reraise so the server/client can retain normal error semantics;
                # the outer error middleware sees the response already started.
                await JSONResponse({"detail": "internal_server_error"}, status_code=500)(
                    scope, receive, traced_send
                )
            raise
        finally:
            try:
                telemetry.finish(context, status)
            except Exception:
                pass
            CURRENT.reset(token)
