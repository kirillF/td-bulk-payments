"""SC-22/23/25: redaction, correlation, signal semantics and failure isolation."""

import concurrent.futures
import json
import threading
import time

import psycopg
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
    with running_app(Settings.local(seeded_database, log_queue_capacity=1)) as app:
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
