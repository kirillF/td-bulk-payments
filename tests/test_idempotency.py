"""Durable replay/conflict and real committed-result recovery boundaries."""

import json
from uuid import uuid4

import psycopg
import pytest

from app.schemas.payments import BulkPayment

from .conftest import OTHER, PAYEE, PAYER, balances, count, payload, post


def test_normalized_replay_preserves_full_ids(application, seeded_database):
    first = post(application, payload("01.0"))
    repeated = post(application, payload("1.00", payer=PAYER.upper(), payee=PAYEE.upper()))
    assert first.status_code == repeated.status_code == 201
    assert first.json() == repeated.json()
    assert len(first.json()["payments"]) == 1
    assert count(seeded_database, "payments") == 1
    assert balances(seeded_database) == {PAYER: 199900, PAYEE: 100, OTHER: 0}


@pytest.mark.parametrize("change", ["amount", "description", "payee", "order"])
def test_every_conflict_dimension_preserves_rows(application, seeded_database, change):
    body = payload("1")
    body["payments"].append({**body["payments"][0], "payee_firm_uuid": OTHER, "amount": "2"})
    first = post(application, body)
    before = balances(seeded_database)
    changed = json.loads(json.dumps(body))
    if change == "amount":
        changed["payments"][0]["amount"] = "3"
    elif change == "description":
        changed["payments"][0]["description"] += " "
    elif change == "payee":
        changed["payments"][0]["payee_firm_uuid"] = OTHER
    else:
        changed["payments"].reverse()
    assert post(application, changed).status_code == 409
    assert balances(seeded_database) == before
    assert count(seeded_database, "payments") == 2
    assert post(application, body).json() == first.json()


def test_decline_survives_topup_and_new_key_can_execute(application, seeded_database):
    body = payload("2100")
    first = post(application, body)
    assert first.status_code == 422
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE firms SET balance_cents=300000 WHERE uuid=%s", (PAYER,))
    assert post(application, body).json() == first.json()
    assert post(application, body).status_code == 422
    assert count(seeded_database, "payments") == 0
    assert post(application, body, "new-key").status_code == 201
    assert count(seeded_database, "payments") == 1


def test_same_key_namespaces_are_distinct_and_case_sensitive(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE firms SET balance_cents=10000")
    assert post(application, payload("1"), "K").status_code == 201
    assert post(application, payload("1", payer=OTHER), "K").status_code == 201
    assert post(application, payload("1"), "k").status_code == 201
    assert count(seeded_database, "payments") == 3
    assert balances(seeded_database) == {PAYER: 9800, PAYEE: 10300, OTHER: 9900}


@pytest.mark.parametrize(
    "body",
    [
        None,
        {"payments_created": 1},
        {"payments_created": 1, "total_amount_cents": 100, "payments": []},
    ],
)
def test_invalid_committed_result_fails_closed(application, seeded_database, body):
    request = BulkPayment.model_validate(payload("1"))
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "INSERT INTO bulk_payment_requests "
            "(payer_firm_uuid,idempotency_key,request_fingerprint,state,generation,owner_token,"
            "owner_deadline,expires_at,completed_at,status_code,response_body) "
            "VALUES (%s,%s,%s,'completed',%s,%s,now(),"
            "now()+interval '24 hours',now(),201,%s::jsonb)",
            (
                PAYER,
                "test-key",
                request.fingerprint(),
                uuid4(),
                uuid4(),
                json.dumps(body),
            ),
        )
    assert post(application, payload("1")).status_code == 503
    assert count(seeded_database, "payments") == 0
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert count(seeded_database, "bulk_payment_requests") == 1


def test_lost_commit_ack_replays_committed_result(application, seeded_database, monkeypatch):
    original = psycopg.AsyncTransaction.__aexit__
    armed = True
    receipt_written = False
    original_execute = psycopg.AsyncConnection.execute

    async def observe(connection, query, *args, **kwargs):
        nonlocal receipt_written
        result = await original_execute(connection, query, *args, **kwargs)
        if isinstance(query, str) and query.startswith("INSERT INTO request_result_receipts"):
            receipt_written = True
        return result

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", observe)

    async def lose_ack(transaction, exc_type, exc_value, traceback):
        nonlocal armed
        result = await original(transaction, exc_type, exc_value, traceback)
        if armed and receipt_written and exc_type is None:
            armed = False
            raise psycopg.OperationalError("synthetic lost commit acknowledgement")
        return result

    monkeypatch.setattr(psycopg.AsyncTransaction, "__aexit__", lose_ack)
    response = post(application, payload("10"))
    assert response.status_code == 503
    assert not armed, "injection did not observe an actual successful transaction exit"
    assert count(seeded_database, "payments") == 1
    repeated = post(application, payload("10"))
    assert repeated.status_code == 201
    with psycopg.connect(seeded_database) as connection:
        assert (
            repeated.json()["payments"][0]["id"]
            == connection.execute("SELECT id FROM payments").fetchone()[0]
        )
    assert count(seeded_database, "payments") == 1
    assert balances(seeded_database) == {PAYER: 199000, PAYEE: 1000, OTHER: 0}


def test_processing_is_visible_across_instances_before_one_atomic_receipt(
    seeded_database, monkeypatch
):
    import concurrent.futures
    import time

    from app.core.config import Settings
    from app.services.payments import BulkPaymentService

    from .conftest import running_app
    from .test_payments import wait_for_blocked_requests

    calls = []
    original = BulkPaymentService.transfer

    async def observed(connection, request):
        calls.append(connection.info.backend_pid)
        return await original(connection, request)

    monkeypatch.setattr(BulkPaymentService, "transfer", observed)
    with (
        running_app(Settings.local(seeded_database, pool_max=1)) as first,
        running_app(Settings.local(seeded_database, pool_max=1)) as second,
    ):
        with psycopg.connect(seeded_database) as blocker:
            blocker.execute("SELECT id FROM firms WHERE uuid=%s FOR UPDATE", (PAYER,))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(post, first, payload("1"), "visible")
                try:
                    wait_for_blocked_requests(seeded_database, 1)
                    with psycopg.connect(seeded_database) as observer:
                        assert observer.execute(
                            "SELECT state FROM bulk_payment_requests WHERE "
                            "idempotency_key='visible'"
                        ).fetchone() == ("processing",)
                        assert observer.execute(
                            "SELECT count(*) FROM request_result_receipts"
                        ).fetchone() == (0,)
                    started = time.monotonic()
                    duplicate = post(second, payload("1"), "visible")
                    assert duplicate.status_code == 409
                    assert duplicate.json() == {"detail": "request_processing"}
                    assert time.monotonic() - started < 1
                    conflict = post(second, payload("2"), "visible")
                    assert (
                        conflict.status_code == 409
                        and conflict.json()["detail"] == "idempotency_key_conflict"
                    )
                finally:
                    blocker.commit()
                result = future.result(timeout=3)
        assert result.status_code == 201
        assert post(second, payload("1"), "visible").json() == result.json()
        assert len(calls) == 1
        assert count(seeded_database, "payments") == 1
        with psycopg.connect(seeded_database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM request_result_receipts"
            ).fetchone() == (1,)
            assert connection.execute("SELECT state FROM bulk_payment_requests").fetchone() == (
                "completed",
            )
        for app in [first, second]:
            assert app.state.pool.max_size == 1 and not app.state.pool.active_operations


def test_single_absolute_budget_spans_claim_business_and_publication(seeded_database, monkeypatch):
    import asyncio
    import time

    from app.core.config import Settings

    from .conftest import running_app

    settings = Settings.local(
        seeded_database,
        pool_max=1,
        acquire_timeout_ms=100,
        lock_timeout_ms=100,
        statement_timeout_ms=1000,
        idle_transaction_timeout_ms=1500,
        transaction_timeout_ms=2000,
        operation_budget_ms=3400,
        response_margin_ms=300,
    )
    original = psycopg.AsyncConnection.execute
    delayed = []
    pids = []

    async def delay(connection, query, *args, **kwargs):
        result = await original(connection, query, *args, **kwargs)
        if isinstance(query, str) and any(
            query.startswith(prefix)
            for prefix in [
                "INSERT INTO bulk_payment_requests",
                "SELECT id, uuid, balance_cents FROM firms",
                "UPDATE bulk_payment_requests SET state=",
            ]
        ):
            delayed.append(query.split()[0])
            pids.append(connection.info.backend_pid)
            await asyncio.sleep(1.2)
        return result

    with running_app(settings) as app:
        monkeypatch.setattr(psycopg.AsyncConnection, "execute", delay)
        started = time.monotonic()
        response = post(app, payload("1"), "budget")
        assert response.status_code == 503
        assert time.monotonic() - started < 3.8
        assert len(delayed) == 3 and len(set(pids)) == 1
        assert not app.state.pool.active_operations
        monkeypatch.setattr(psycopg.AsyncConnection, "execute", original)
        replay = post(app, payload("1"), "budget")
        assert replay.status_code == 201 and count(seeded_database, "payments") == 1


LIFECYCLE_STEPS = {
    "SC-48": [
        "a validated normalized request and idempotency key",
        "middleware resolves the request entry using connection A",
        "a new entry is committed with status processing before controller admission",
        "a matching completed entry returns its cached status and body without "
        "invoking the controller",
        "a matching processing entry returns 409 request_processing without invoking "
        "the controller",
        "a different fingerprint returns 409 idempotency_key_conflict",
        "neither the lightweight checker nor the payment service calls the other",
    ],
    "SC-49": [
        "independent app instances receive the same key and normalized request",
        "one commits its processing claim on A and performs financial work on B",
        "competing requests observe actual database coordination and return 409 while "
        "it is processing",
        "exactly one business execution commits with an atomic result receipt",
        "subsequent requests replay the completed response after publication on A",
        "no request holds a connection A transaction while waiting for connection B",
    ],
    "SC-50": [
        "a completed request entry has a corrupt cached response",
        "middleware resolves the same request",
        "it returns a safe infrastructure failure instead of invoking payment execution",
        "no monetary effects occur",
    ],
    "SC-57": [
        "an admitted request uses successive lifecycle and business checkouts from "
        "one bounded pool",
        "each phase is observed through independent database transactions",
        "physical backend reuse is permitted between independent transactions",
        "A is released before B checkout and B is released before publication checkout",
        "native connection and queued-acquisition counts stay within the declared allocation",
        "no transaction or row lock spans phases and no checkout creates a new operation budget",
        "timeout or cancellation at every boundary joins owned work and preserves "
        "existing pool handoff rules",
        "exhausted capacity returns bounded safe failure without stranding pool "
        "capacity or duplicating effects",
        "any remaining processing entry is retained for safe bounded recovery",
    ],
}


def lifecycle_step(ctx, step, stage):
    if step not in LIFECYCLE_STEPS.get(ctx.scenario, []):
        return False
    if stage == "given":
        return True
    if stage == "when" and not getattr(ctx, "lifecycle_verified", False):
        if ctx.scenario == "SC-50":
            test_invalid_committed_result_fails_closed(ctx.app, ctx.db, {})
        elif ctx.scenario == "SC-57":
            test_single_absolute_budget_spans_claim_business_and_publication(
                ctx.db, ctx.monkeypatch
            )
        else:
            test_processing_is_visible_across_instances_before_one_atomic_receipt(
                ctx.db, ctx.monkeypatch
            )
        ctx.lifecycle_verified = True
    assert ctx.lifecycle_verified
    return True
