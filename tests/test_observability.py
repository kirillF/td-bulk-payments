"""SC-22/23/25: redaction, correlation, signal semantics and failure isolation."""

import concurrent.futures
import json
import threading
import time

import psycopg
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.config import Settings
from app.core.observability import BoundedSpanProcessor, SafeExporter

from .conftest import OTHER, PAYEE, PAYER, balances, count, payload, post, running_app


def wait_events(logs, number):
    deadline = time.monotonic() + 2
    while len([x for x in logs if json.loads(x)["event"] == "http.request.completed"]) < number:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    from datetime import datetime, timedelta

    events = [json.loads(x) for x in logs]
    for event in events:
        assert event["timestamp"].endswith("Z")
        assert datetime.fromisoformat(event["timestamp"]).utcoffset() == timedelta(0)
    return [x for x in events if x["event"] == "http.request.completed"]


def test_signals_redact_payload_and_preserve_trace_context(application, seeded_database):
    telemetry = application.state.telemetry
    logs = []
    telemetry.sink = logs.append
    memory = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(SafeExporter(memory, telemetry.drop)))
    body = payload("1")
    body["payments"][0]["description"] = "CANARY financial payload"
    # Use a real ASGI request with hostile request-id/baggage and valid W3C context.
    first = application.state.test_client.post(
        "/v1/bulk-payments",
        json=body,
        headers={
            "Idempotency-Key": "CANARY-key",
            "X-Request-ID": "CANARY-request",
            "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            "baggage": "secret=CANARY",
        },
    )
    assert first.status_code == 201
    assert post(application, body, "CANARY-key").json() == first.json()
    events = wait_events(logs, 2)
    assert events[0]["request_id"] == first.headers["X-Request-ID"]
    assert events[0]["trace_id"] == "1" * 32
    assert events[0]["request_id"] != events[1]["request_id"]
    assert [e["outcome"] for e in events] == ["committed", "replayed"]
    assert telemetry.registry.get_sample_value("firm_payments_created_total") == 1
    assert (
        telemetry.registry.get_sample_value("firm_payments_attempts_total", {"outcome": "replayed"})
        == 1
    )
    spans = memory.get_finished_spans()
    assert any(s.name == "payments.persist" for s in spans)
    replay_trace = events[1]["trace_id"]
    assert not any(
        s.name in {"payments.persist", "transaction.commit"}
        and f"{s.context.trace_id:032x}" == replay_trace
        for s in spans
    )
    signals = (
        json.dumps(events)
        + str(
            [
                (s.name, dict(s.attributes or {}), s.events, dict(s.resource.attributes))
                for s in spans
            ]
        )
        + telemetry.metrics(application.state.pool).decode()
    )
    for canary in ["CANARY", PAYER, PAYEE, "CANARY-key", "CANARY financial payload"]:
        assert canary not in signals


def test_outcomes_and_app_registries_are_independent(application, seeded_database):
    logs = []
    application.state.telemetry.sink = logs.append
    assert post(application, payload("1")).status_code == 201
    assert post(application, payload("2")).status_code == 409
    assert post(application, payload("9999"), "decline").status_code == 422
    assert post(application, payload("0"), "invalid").status_code == 422
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "CREATE FUNCTION fail_signal() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'CANARY driver details'; END $$; "
            "CREATE TRIGGER fail_signal BEFORE INSERT ON payments "
            "FOR EACH ROW EXECUTE FUNCTION fail_signal()"
        )
    try:
        assert post(application, payload("1"), "error").status_code == 503
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute("DROP TRIGGER fail_signal ON payments; DROP FUNCTION fail_signal()")
    events = wait_events(logs, 5)
    assert {e["outcome"] for e in events} == {
        "committed",
        "conflict",
        "business_rejected",
        "validation_rejected",
        "transient_failure",
    }
    assert "CANARY" not in json.dumps(events)
    with running_app(seeded_database) as second:
        assert second.state.telemetry.registry is not application.state.telemetry.registry
        assert second.state.telemetry.provider is not application.state.telemetry.provider
        assert second.state.telemetry.registry.get_sample_value("firm_payments_created_total") == 0


def test_overlapping_request_context_does_not_leak(application, seeded_database):
    logs = []
    application.state.telemetry.sink = logs.append
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda i: post(application, payload("1"), f"context-{i}"), range(4))
        )
    assert all(r.status_code == 201 for r in results)
    events = wait_events(logs, 4)
    assert len({e["request_id"] for e in events}) == 4
    assert {e["request_id"] for e in events} == {r.headers["X-Request-ID"] for r in results}
    assert len({e["trace_id"] for e in events}) == 4


class BrokenExporter(SpanExporter):
    def export(self, spans):
        raise RuntimeError("CANARY exporter")

    def shutdown(self):
        pass


def test_full_queue_broken_sink_and_exporter_preserve_money(seeded_database, monkeypatch):
    release = threading.Event()
    with running_app(
        Settings.local(seeded_database, log_queue_capacity=1, log_level="DEBUG")
    ) as app:
        telemetry = app.state.telemetry
        telemetry.sink = lambda _: release.wait(3)
        telemetry.provider.add_span_processor(
            SimpleSpanProcessor(SafeExporter(BrokenExporter(), telemetry.drop))
        )
        try:
            first = post(app, payload("1"), "safe")
            for i in range(6):
                assert post(app, payload("1"), f"pressure-{i}").status_code == 201
            assert post(app, payload("1"), "safe").json() == first.json()
            assert count(seeded_database, "payments") == 7
            assert balances(seeded_database) == {PAYER: 199300, PAYEE: 700, OTHER: 0}
            assert (
                telemetry.registry.get_sample_value(
                    "firm_payments_telemetry_dropped_total", {"signal": "logs"}
                )
                > 0
            )
            assert (
                telemetry.registry.get_sample_value(
                    "firm_payments_telemetry_dropped_total", {"signal": "traces"}
                )
                > 0
            )
            start = time.monotonic()
            telemetry.close()
            assert time.monotonic() - start < 1
        finally:
            release.set()


def test_optional_otlp_unavailable_endpoint_is_bounded(seeded_database, monkeypatch):
    settings = Settings.local(
        seeded_database,
        otlp_endpoint="http://127.0.0.1:1/v1/traces",
        trace_queue_capacity=2,
        otlp_timeout_ms=100,
    )
    with running_app(settings) as app:
        first = post(app, payload("1"), "otlp")
        assert first.status_code == 201
        assert post(app, payload("1"), "otlp").json() == first.json()
        deadline = time.monotonic() + 2
        while not app.state.telemetry.registry.get_sample_value(
            "firm_payments_telemetry_dropped_total", {"signal": "traces"}
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert count(seeded_database, "payments") == 1
        start = time.monotonic()
        app.state.telemetry.close()
        assert time.monotonic() - start < 1


def test_successful_slow_trace_exporter_overflow_is_observable(application, seeded_database):
    release, entered = threading.Event(), threading.Event()

    class GatedExporter(SpanExporter):
        def export(self, spans):
            entered.set()
            assert release.wait(3)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    telemetry = application.state.telemetry
    processor = BoundedSpanProcessor(
        GatedExporter(), telemetry.drop, capacity=1, shutdown_seconds=0.1
    )
    telemetry.provider.add_span_processor(processor)
    try:
        first = post(application, payload("1"), "overflow")
        assert entered.wait(1)
        assert first.status_code == 201
        assert post(application, payload("1"), "overflow").json() == first.json()
        assert (
            telemetry.registry.get_sample_value(
                "firm_payments_telemetry_dropped_total", {"signal": "traces"}
            )
            > 0
        )
        assert count(seeded_database, "payments") == 1
        assert balances(seeded_database) == {PAYER: 199900, PAYEE: 100, OTHER: 0}
    finally:
        release.set()
    assert processor.force_flush(1000)


CORRELATION_CASES = {
    "absent": [],
    "empty": [("X-Correlation-ID", "")],
    "invalid characters": [("X-Correlation-ID", "CANARY/invalid")],
    "oversized": [("X-Correlation-ID", "x" * 129)],
    "duplicate headers": [("X-Correlation-ID", "CANARY-one"), ("x-correlation-id", "CANARY-two")],
    "comma-combined values": [("X-Correlation-ID", "CANARY-one,CANARY-two")],
}


@pytest.mark.parametrize("metadata", list(CORRELATION_CASES))
@pytest.mark.parametrize("status", [201, 422, 409, 503, 404, 500])
def test_correlation_fallback_all_statuses(application, monkeypatch, metadata, status):
    import re

    from psycopg_pool import PoolTimeout

    logs = []
    application.state.telemetry.sink = logs.append
    client = application.state.test_client
    before = 0
    if status == 409:
        assert post(application, payload("1"), "metadata").status_code == 201
        wait_events(logs, 1)
        before = 1
    if status == 503:

        async def unavailable(*args, **kwargs):
            raise PoolTimeout("CANARY private driver text")

        monkeypatch.setattr(application.state.pool, "getconn", unavailable)
    if status == 500:

        async def broken():
            raise RuntimeError("CANARY private exception")

        application.add_api_route("/broken", broken, methods=["GET"])
    headers = [("Idempotency-Key", "metadata"), *CORRELATION_CASES[metadata]]
    response = (
        client.get("/missing" if status == 404 else "/broken", headers=headers)
        if status in {404, 500}
        else client.post(
            "/v1/bulk-payments",
            headers=headers,
            json=payload("0" if status == 422 else "2" if status == 409 else "1"),
        )
    )
    assert response.status_code == status
    assert len(response.headers.get_list("X-Correlation-ID")) == 1
    correlation = response.headers["X-Correlation-ID"]
    assert re.fullmatch("[0-9a-f]{32}", correlation)
    events = wait_events(logs, before + 1)
    complete = next(e for e in events if e["request_id"] == response.headers["X-Request-ID"])
    arrived = [
        json.loads(x)
        for x in logs
        if json.loads(x)["event"] == "http.request.arrived"
        and json.loads(x)["request_id"] == complete["request_id"]
    ]
    assert len(arrived) == 1
    assert arrived[0]["correlation_id"] == complete["correlation_id"] == correlation
    assert complete["status_code"] == status
    if status >= 500:
        assert complete["level"] in {"WARNING", "ERROR"}
        assert complete.get("error_type")
    assert "CANARY" not in "".join(logs) + response.text + str(response.headers)


def test_valid_correlation_replay_and_debug_stages(
    seeded_database, first_correlation="attempt-one"
):
    import logging

    names = ["psycopg", "psycopg.pool", "uvicorn.access", "uvicorn.error", "opentelemetry"]
    original_handlers = {name: list(logging.getLogger(name).handlers) for name in names}
    original_propagation = {name: logging.getLogger(name).propagate for name in names}
    with running_app(Settings.local(seeded_database, log_level="DEBUG")) as app:
        logs = []
        app.state.telemetry.sink = logs.append
        body = payload("1")
        body["payments"] = [dict(body["payments"][0], description="CANARY private payment")] * 2
        first = app.state.test_client.post(
            "/v1/bulk-payments",
            json=body,
            headers={
                "Idempotency-Key": "CANARY-key",
                "X-Correlation-ID": first_correlation,
            },
        )
        second = app.state.test_client.post(
            "/v1/bulk-payments",
            json=body,
            headers={
                "Idempotency-Key": "CANARY-key",
                "X-Correlation-ID": "attempt-two",
            },
        )
        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        assert first.headers["X-Correlation-ID"] == first_correlation
        assert second.headers["X-Correlation-ID"] == "attempt-two"
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
        events = wait_events(logs, 2)
        assert [e["correlation_id"] for e in events] == [first_correlation, "attempt-two"]
        decoded = [json.loads(x) for x in logs]
        stages = [e for e in decoded if e["event"].startswith("stage.")]
        assert stages and all(e["level"] == "DEBUG" for e in stages)
        expected = {
            "request.prepare",
            "idempotency.admission",
            "idempotency.claim",
            "controller.execute",
            "idempotency.guard",
            "firms.lock",
            "payments.plan",
            "payments.balances",
            "payments.insert",
            "idempotency.receipt",
            "transaction.commit",
            "idempotency.publish",
        }
        first_stages = [e for e in stages if e["request_id"] == first.headers["X-Request-ID"]]
        assert expected <= {e["stage"] for e in first_stages}
        assert all(e["correlation_id"] == first_correlation for e in first_stages)
        inserts = [
            e
            for e in first_stages
            if e["stage"] == "payments.insert" and e["event"] == "stage.completed"
        ]
        assert [e["ordinal"] for e in inserts] == [1, 2]
        committed = next(
            i
            for i, e in enumerate(decoded)
            if e["event"] == "stage.completed" and e.get("stage") == "transaction.commit"
        )
        completed = next(i for i, e in enumerate(decoded) if e["event"] == "http.request.completed")
        assert committed < completed
        serialized = "".join(logs)
        assert all(secret not in serialized for secret in ["CANARY", PAYER, PAYEE, "amount_cents"])
        assert app.state.telemetry.logger.logger.name == "firm_payments"
        assert count(seeded_database, "payments") == 2
    assert all(logging.getLogger(n).handlers == original_handlers[n] for n in names)
    assert all(logging.getLogger(n).propagate == original_propagation[n] for n in names)


def test_info_logs_only_lifecycle_and_classified_failure(application, monkeypatch):
    from psycopg_pool import PoolTimeout

    logs = []
    application.state.telemetry.sink = logs.append
    assert post(application, payload(), "info").status_code == 201
    assert post(application, payload("0"), "invalid").status_code == 422
    assert post(application, payload("2"), "info").status_code == 409

    async def fail(*args, **kwargs):
        raise PoolTimeout("CANARY unsafe error")

    monkeypatch.setattr(application.state.pool, "getconn", fail)
    assert post(application, payload(), "failure").status_code == 503
    complete = wait_events(logs, 4)
    events = [json.loads(x) for x in logs]
    assert len([e for e in events if e["event"] == "http.request.arrived"]) == 4
    assert not any(e["event"].startswith("stage.") for e in events)
    assert not any(e["event"] == "database.cleanup.failed" for e in events)
    failures = [e for e in complete if e["status_code"] == 503]
    assert len(failures) == 1 and failures[0]["level"] == "WARNING"
    assert failures[0]["error_code"] == "pool_exhausted"
    assert "CANARY" not in "".join(logs)


def test_context_isolation_for_timeout_cancellation_and_success(seeded_database):
    import asyncio
    from uuid import uuid4

    import httpx
    import pytest

    from app.core.request_context import CURRENT

    from .test_payments import wait_for_blocked_requests

    extra = str(uuid4())
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE firms SET balance_cents=10000 WHERE uuid=%s", (OTHER,))
        connection.execute(
            "INSERT INTO firms(name,balance_cents,uuid) VALUES ('Fourth',0,%s)", (extra,)
        )
    settings = Settings.local(
        seeded_database,
        log_level="DEBUG",
        pool_max=2,
        acquire_timeout_ms=100,
        lock_timeout_ms=500,
        statement_timeout_ms=1000,
        idle_transaction_timeout_ms=1500,
        transaction_timeout_ms=2000,
        operation_budget_ms=3400,
        response_margin_ms=300,
    )
    with running_app(settings) as first, running_app(settings) as second:
        left, right = [], []
        first.state.telemetry.sink = left.append
        second.state.telemetry.sink = right.append
        with psycopg.connect(seeded_database) as blocker:
            blocker.execute("SELECT id FROM firms WHERE uuid=%s FOR UPDATE", (PAYEE,))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    first.state.test_client.post,
                    "/v1/bulk-payments",
                    json=payload("1"),
                    headers={"Idempotency-Key": "slow", "X-Correlation-ID": "slow"},
                )
                healthy = second.state.test_client.post(
                    "/v1/bulk-payments",
                    json=payload("1", payer=OTHER, payee=extra),
                    headers={"Idempotency-Key": "healthy", "X-Correlation-ID": "healthy"},
                )
                assert healthy.status_code == 201

                async def cancel_request():
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=second), base_url="http://test"
                    ) as client:
                        action = asyncio.create_task(
                            client.post(
                                "/v1/bulk-payments",
                                json=payload("1"),
                                headers={"Idempotency-Key": "cancel", "X-Correlation-ID": "cancel"},
                            )
                        )
                        await asyncio.to_thread(wait_for_blocked_requests, seeded_database, 2)
                        action.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await action
                        assert not second.state.pool.active_operations

                second.state.test_client.portal.call(cancel_request)
                result = future.result(timeout=3)
                assert result.status_code == 503
            blocker.rollback()
        wait_events(left, 1)
        wait_events(right, 2)
        events = [json.loads(x) for x in left + right]
        pairs = {}
        for event in events:
            if "request_id" in event:
                pairs.setdefault(event["correlation_id"], set()).add(event["request_id"])
        assert set(pairs) == {"slow", "healthy", "cancel"}
        assert all(len(ids) == 1 for ids in pairs.values())
        assert len(set.union(*pairs.values())) == 3
        assert any(
            e.get("correlation_id") == "cancel" and e["event"] == "stage.failed" for e in events
        )
        assert first.state.test_client.portal.call(CURRENT.get) is None
        assert second.state.test_client.portal.call(CURRENT.get) is None
        second.state.telemetry.event("service.readiness", ready=True)
        deadline = time.monotonic() + 1
        while not any(json.loads(x)["event"] == "service.readiness" for x in right):
            assert time.monotonic() < deadline
            time.sleep(0.005)
        background = next(
            json.loads(x) for x in right if json.loads(x)["event"] == "service.readiness"
        )
        assert "request_id" not in background and "correlation_id" not in background
        assert count(seeded_database, "payments") == 1


CORRELATION_STEPS = {
    "SC-39": [
        'a valid payment request with one X-Correlation-ID header "firm-job_2026.10"',
        "the request completes",
        "its response echoes that correlation ID and a fresh X-Request-ID",
        "controller, service and storage events share the effective correlation and request IDs",
    ],
    "SC-40": [
        "correlation metadata is <metadata>",
        "an application HTTP request returns <status>",
        "exactly one safe generated correlation ID is returned",
        "raw invalid metadata is absent from logs and responses",
        "the payment validation and execution decision is unaffected by correlation replacement",
    ],
    "SC-41": [
        'a payment committed under a key with correlation ID "attempt-one"',
        'the same normalized request and key use correlation ID "attempt-two"',
        "the original status, payment list and IDs are replayed without new monetary effects",
        'the response and logs use "attempt-two" and a fresh request ID',
    ],
    "SC-42": [
        "the log level is INFO and the sink is healthy",
        "requests complete with success, rejection, conflict and infrastructure failure",
        "each application request has one arrival and one completion event with status and outcome",
        "infrastructure errors have a classified warning or error without "
        "duplicated exception reports",
        "routine processing-stage events are suppressed",
    ],
    "SC-43": [
        "the log level is DEBUG and the sink is healthy",
        "a batch executes through controller, idempotency, locks, planning, writes and commit",
        "structured start and finish or failure events identify each processing "
        "stage and storage operation",
        "per-payment insertion events identify only progress ordinal rather than "
        "financial identifiers",
        "all levels exclude financial payloads, credentials, SQL parameters and raw exception text",
        "an attempted write is not reported as committed before commit acknowledgement",
    ],
    "SC-44": [
        "overlapping requests on independent app instances include distinct correlation IDs",
        "one times out, one is cancelled and others complete",
        "their handler, service, storage and cleanup events retain their own request context",
        "context is reset before subsequent requests and background lifecycle events",
        "unknown-commit and rollback failures retain truthful safe classifications",
    ],
    "SC-47": [
        "a slow or failing log sink and a saturated bounded queue",
        "payment requests execute with verbose logging",
        "monetary and idempotency invariants remain intact within existing operation budgets",
        "requests do not wait for the external sink",
        "telemetry drop signals expose loss without recursive failure logging",
    ],
}


def correlation_step(ctx, step, stage):
    import re

    if ctx.scenario == "SC-40":
        match = re.fullmatch(r"correlation metadata is (.+)", step)
        if match:
            ctx.correlation_case = match[1]
            return True
        match = re.fullmatch(r"an application HTTP request returns (\d+)", step)
        if match:
            test_correlation_fallback_all_statuses(
                ctx.app, ctx.monkeypatch, ctx.correlation_case, int(match[1])
            )
            ctx.correlation_verified = True
            return True
    if step not in CORRELATION_STEPS.get(ctx.scenario, []):
        return False
    if stage == "given":
        return True
    if stage == "when" and not getattr(ctx, "correlation_verified", False):
        if ctx.scenario == "SC-42":
            test_info_logs_only_lifecycle_and_classified_failure(ctx.app, ctx.monkeypatch)
        elif ctx.scenario == "SC-44":
            test_context_isolation_for_timeout_cancellation_and_success(ctx.db)
        elif ctx.scenario == "SC-47":
            test_full_queue_broken_sink_and_exporter_preserve_money(ctx.db, ctx.monkeypatch)
        else:
            test_valid_correlation_replay_and_debug_stages(
                ctx.db, "firm-job_2026.10" if ctx.scenario == "SC-39" else "attempt-one"
            )
        ctx.correlation_verified = True
    assert ctx.correlation_verified
    return True


ROUTE_CASES = {
    "extra_method": (
        "PATCH",
        "/configured/items/raw-secret",
        "/configured/items/{item}",
        "PATCH",
        200,
        False,
    ),
    "second_method": (
        "DELETE",
        "/configured/items/raw-secret",
        "/configured/items/{item}",
        "DELETE",
        200,
        False,
    ),
    "parameterized": (
        "PATCH",
        "/configured/items/another-secret",
        "/configured/items/{item}",
        "PATCH",
        200,
        False,
    ),
    "changed_prefix": (
        "PATCH",
        "/alternate/items/raw-secret",
        "/alternate/items/{item}",
        "PATCH",
        200,
        False,
    ),
    "unsupported": (
        "HOSTILE-METHOD",
        "/configured/items/raw-secret",
        "/configured/items/{item}",
        "OTHER",
        405,
        False,
    ),
    "configured_probe": (
        "GET",
        "/configured/custom-probe",
        "/configured/custom-probe",
        "GET",
        200,
        True,
    ),
}


def configured_route_signals(case):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from app.api.route_metadata import ProbeRoute, RouteComposition
    from app.core.observability import RequestTelemetryMiddleware, Telemetry

    method, path, template, label, status, probe = ROUTE_CASES[case]
    application = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    router = APIRouter()

    async def item(item: str):
        return {"ok": True}

    router.add_api_route("/items/{item}", item, methods=["PATCH", "DELETE"])
    probes = APIRouter(route_class=ProbeRoute)

    @probes.get("/custom-probe")
    async def custom_probe():
        return {"ok": True}

    composition = RouteComposition(application)
    prefix = "/alternate" if case == "changed_prefix" else "/configured"
    composition.include(router, prefix=prefix)
    composition.include(probes, prefix=prefix)
    describe = composition.freeze()
    telemetry = Telemetry(settings=Settings.local("postgresql://unused"), request_metadata=describe)
    logs = []
    telemetry.sink = logs.append
    memory = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(SafeExporter(memory, telemetry.drop)))
    application.state.telemetry = telemetry
    application.add_middleware(RequestTelemetryMiddleware, owner=application)
    try:
        with TestClient(application) as client:
            response = client.request(method, path)
            assert response.status_code == status
            complete = wait_events(logs, 1)[0]
            arrived = next(
                json.loads(x) for x in logs if json.loads(x)["event"] == "http.request.arrived"
            )
            for event in [arrived, complete]:
                assert event["route"] == template
                assert event["method"] == label
            spans = memory.get_finished_spans()
            assert any(s.attributes.get("http.route") == template for s in spans)
            metrics = telemetry.metrics(type("Pool", (), {"get_stats": lambda self: {}})()).decode()
            signals = "".join(logs) + metrics + str([dict(s.attributes) for s in spans])
            assert "raw-secret" not in signals and "another-secret" not in signals
            assert "HOSTILE-METHOD" not in signals
            attempts = telemetry.registry.get_sample_value(
                "firm_payments_attempts_total", {"outcome": "observed"}
            )
            assert attempts in {None, 0} if probe or status >= 400 else attempts == 1
            head = client.head(prefix + "/custom-probe")
            assert head.status_code == 405
            head_event = wait_events(logs, 2)[-1]
            assert (head_event["route"], head_event["method"]) == (
                prefix + "/custom-probe",
                "OTHER",
            )
            # Missing paths and unsupported methods remain finite.
            missing = client.request("HOSTILE-METHOD", prefix + "/unregistered-secret")
            assert missing.status_code == 404
            last = wait_events(logs, 3)[-1]
            assert (last["route"], last["method"]) == ("unmatched", "OTHER")
            assert "unregistered-secret" not in "".join(logs)
            with pytest.raises(ValueError, match="frozen"):
                composition.include(router)
    finally:
        telemetry.close()


@pytest.mark.parametrize("case", list(ROUTE_CASES))
def test_configured_route_signals(case):
    configured_route_signals(case)


def test_route_metadata_failure_is_safe_and_instances_are_independent():
    from fastapi import APIRouter, FastAPI

    from app.api.route_metadata import RouteComposition
    from app.core.observability import Telemetry

    router = APIRouter()

    @router.put("/entry")
    async def entry():
        return {}

    one, two = RouteComposition(FastAPI()), RouteComposition(FastAPI())
    one.include(router, prefix="/one")
    two.include(router, prefix="/two")
    scope = {"type": "http", "path": "/one/entry", "method": "PUT", "root_path": ""}
    assert one.freeze()(scope).route == "/one/entry"
    assert two.freeze()(scope).route == "unmatched"

    def broken(scope):
        raise RuntimeError("private route metadata")

    telemetry = Telemetry(settings=Settings.local("postgresql://unused"), request_metadata=broken)
    try:
        context = telemetry.begin(scope)
        assert (context.route, context.method) == ("unmatched", "OTHER")
        telemetry.finish(context, 200)
        assert (
            telemetry.registry.get_sample_value(
                "firm_payments_telemetry_dropped_total", {"signal": "metrics"}
            )
            == 1
        )
    finally:
        telemetry.close()
