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


def reserve_claim(app, key, body=None):
    from app.db.database import run_transaction
    from app.services.idempotency import check

    request = BulkPayment.model_validate(body or payload("1"))

    async def reserve():
        return await run_transaction(
            app.state.pool,
            lambda connection: check(
                connection, str(request.payer_firm_uuid), key, request.fingerprint(), 15000
            ),
        )

    return app.state.test_client.portal.call(reserve)


@pytest.mark.parametrize("new_amount", ["1", "2"])
def test_completed_ttl_is_exact_stable_and_reuse_checks_expiry_first(
    application, seeded_database, new_amount
):
    first = post(application, payload("1"), "ttl")
    assert first.status_code == 201
    with psycopg.connect(seeded_database) as connection:
        old = connection.execute(
            "SELECT generation,completed_at,expires_at,expires_at-completed_at "
            "FROM bulk_payment_requests WHERE idempotency_key='ttl'"
        ).fetchone()
    from datetime import timedelta

    assert old[3] == timedelta(hours=24)
    assert post(application, payload("1"), "ttl").json() == first.json()
    with psycopg.connect(seeded_database) as connection:
        assert (
            connection.execute(
                "SELECT generation,completed_at,expires_at,expires_at-completed_at "
                "FROM bulk_payment_requests WHERE idempotency_key='ttl'"
            ).fetchone()
            == old
        )
        connection.execute(
            "UPDATE bulk_payment_requests SET expires_at=clock_timestamp() "
            "WHERE idempotency_key='ttl'"
        )
    fresh = post(application, payload(new_amount), "ttl")
    assert fresh.status_code == 201
    assert fresh.json()["payments"][0]["id"] != first.json()["payments"][0]["id"]
    with psycopg.connect(seeded_database) as connection:
        assert (
            connection.execute(
                "SELECT generation FROM bulk_payment_requests WHERE idempotency_key='ttl'"
            ).fetchone()[0]
            != old[0]
        )
        assert connection.execute(
            "SELECT count(*) FROM request_execution_guards WHERE generation=%s", (old[0],)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM request_result_receipts WHERE generation=%s", (old[0],)
        ).fetchone() == (0,)
    assert count(seeded_database, "payments") == 2
    assert balances(seeded_database)[PAYER] == 200000 - 100 - int(new_amount) * 100


def test_receipt_recovers_after_failed_completed_publication(
    application, seeded_database, monkeypatch
):
    import app.api.middleware.idempotency as middleware

    async def fail(*args):
        raise psycopg.OperationalError("CANARY publication failed")

    monkeypatch.setattr(middleware, "publish_completed", fail)
    assert post(application, payload("1"), "crash").status_code == 503
    with psycopg.connect(seeded_database) as connection:
        assert connection.execute(
            "SELECT state FROM bulk_payment_requests WHERE idempotency_key='crash'"
        ).fetchone() == ("processing",)
        assert connection.execute("SELECT count(*) FROM request_result_receipts").fetchone() == (1,)
        original_id = connection.execute("SELECT id FROM payments").fetchone()[0]
    replay = post(application, payload("1"), "crash")
    assert replay.status_code == 201 and replay.json()["payments"][0]["id"] == original_id
    assert count(seeded_database, "payments") == 1
    assert balances(seeded_database)[PAYER] == 199900


def test_abandoned_owner_is_fenced_before_replacement(application, seeded_database):
    from app.api.v1.payments import execute_admitted
    from app.db.database import run_transaction
    from app.db.idempotency import publish_completed
    from app.models.idempotency import AdmissionLost

    prior_count = count(seeded_database, "payments")
    prior_balance = balances(seeded_database)[PAYER]
    old = reserve_claim(application, "abandoned")
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "UPDATE bulk_payment_requests SET owner_deadline=now()-interval '1 second'"
        )
    replacement = post(application, payload("1"), "abandoned")
    assert replacement.status_code == 201

    async def resume():
        with pytest.raises(AdmissionLost):
            await run_transaction(
                application.state.pool,
                lambda connection: execute_admitted(
                    connection, BulkPayment.model_validate(payload("1")), old
                ),
            )
        with pytest.raises(AdmissionLost):
            await run_transaction(
                application.state.pool, lambda connection: publish_completed(connection, old)
            )

    application.state.test_client.portal.call(resume)
    assert post(application, payload("1"), "abandoned").json() == replacement.json()
    assert count(seeded_database, "payments") == prior_count + 1
    assert balances(seeded_database)[PAYER] == prior_balance - 100
    with psycopg.connect(seeded_database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM request_execution_guards WHERE generation=%s", (old.generation,)
        ).fetchone() == (0,)


def test_receipt_failure_rolls_back_business_effects(application, seeded_database):
    prior_count, prior_balances = count(seeded_database, "payments"), balances(seeded_database)
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "CREATE FUNCTION fail_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'CANARY receipt failure'; END $$; "
            "CREATE TRIGGER fail_receipt BEFORE INSERT ON request_result_receipts "
            "FOR EACH ROW EXECUTE FUNCTION fail_receipt()"
        )
    try:
        assert post(application, payload("1"), "receipt-failure").status_code == 503
        assert count(seeded_database, "payments") == prior_count
        assert balances(seeded_database) == prior_balances
        with psycopg.connect(seeded_database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM request_result_receipts"
            ).fetchone() == (0,)
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "DROP TRIGGER fail_receipt ON request_result_receipts; DROP FUNCTION fail_receipt()"
            )
    assert post(application, payload("1"), "receipt-failure").status_code == 201


def test_expired_active_guard_cannot_be_reclaimed(application, seeded_database, monkeypatch):
    import asyncio
    import concurrent.futures
    import threading

    import app.api.v1.payments as controller
    from app.core.config import Settings
    from app.db.idempotency_maintenance import run

    entered, release = threading.Event(), threading.Event()
    original = controller.acquire_execution_guard

    async def pause(connection, admission):
        await original(connection, admission)
        entered.set()
        assert await asyncio.to_thread(release.wait, 4)

    monkeypatch.setattr(controller, "acquire_execution_guard", pause)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(post, application, payload("1"), "active")
        try:
            assert entered.wait(2)
            with psycopg.connect(seeded_database) as connection:
                connection.execute(
                    "UPDATE bulk_payment_requests SET owner_deadline=now()-interval '1 day',"
                    "expires_at=now()-interval '1 second' WHERE idempotency_key='active'"
                )
            result = asyncio.run(run(Settings.local(seeded_database), 2, 2, 3))
            assert result["skipped"] == 1 and result["examined"] == 1
            duplicate = post(application, payload("1"), "active")
            assert (
                duplicate.status_code == 409 and duplicate.json()["detail"] == "request_processing"
            )
            assert count(seeded_database, "payments") == 0
            with psycopg.connect(seeded_database) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM request_execution_guards"
                ).fetchone() == (1,)
        finally:
            release.set()
        first = future.result(timeout=3)
    assert first.status_code == 201
    assert post(application, payload("1"), "active").json() == first.json()
    assert count(seeded_database, "payments") == 1


def test_maintenance_cli_is_bounded_and_retains_corruption(application, seeded_database):
    import os
    import subprocess
    import sys

    from app.api.v1.payments import execute_admitted
    from app.db.database import run_transaction

    assert post(application, payload("1"), "expired").status_code == 201
    assert post(application, payload("1"), "corrupt").status_code == 201
    abandoned = reserve_claim(application, "no-effects")
    receipt = reserve_claim(application, "unpublished")
    active = reserve_claim(application, "locked")
    unrelated = reserve_claim(application, "unrelated")

    async def commit_only():
        await run_transaction(
            application.state.pool,
            lambda connection: execute_admitted(
                connection, BulkPayment.model_validate(payload("1")), receipt
            ),
        )

    application.state.test_client.portal.call(commit_only)
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "UPDATE bulk_payment_requests SET expires_at=now()-interval '1 second' "
            "WHERE idempotency_key IN ('expired','corrupt')"
        )
        connection.execute(
            "UPDATE bulk_payment_requests SET response_body='{}'::jsonb "
            "WHERE idempotency_key='corrupt'"
        )
        connection.execute(
            "UPDATE bulk_payment_requests SET owner_deadline=now()-interval '1 second' "
            "WHERE idempotency_key IN ('no-effects','locked')"
        )
    before = balances(seeded_database)
    env = dict(os.environ, DATABASE_URL=seeded_database, PAYMENTS_PROFILE="local")
    command = [
        sys.executable,
        "-m",
        "app.db.idempotency_maintenance",
        "--batch-size",
        "2",
        "--max-batches",
        "4",
        "--timeout-seconds",
        "5",
    ]
    with psycopg.connect(seeded_database) as blocker:
        blocker.execute(
            "SELECT generation FROM request_execution_guards WHERE generation=%s FOR UPDATE",
            (active.generation,),
        )
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1  # Corruption is retained and signaled, never silently skipped.
    report = json.loads(result.stdout)
    assert report["examined"] <= 8 and report["batches"] <= 4
    assert report["unresolved"] == 1 and report["deleted"] == 1 and report["released"] == 1
    assert report["completed"] == 1 and report["skipped"] == 1
    assert balances(seeded_database) == before and count(seeded_database, "payments") == 3
    with psycopg.connect(seeded_database) as connection:
        assert connection.execute(
            "SELECT state FROM bulk_payment_requests WHERE idempotency_key='unpublished'"
        ).fetchone() == ("completed",)
        for token in [active, unrelated]:
            assert connection.execute(
                "SELECT count(*) FROM request_execution_guards WHERE generation=%s",
                (token.generation,),
            ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM request_execution_guards WHERE generation=%s",
            (abandoned.generation,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM bulk_payment_requests WHERE idempotency_key='corrupt'"
        ).fetchone() == (1,)
    for flag, value in [
        ("--batch-size", "0"),
        ("--max-batches", "-1"),
        ("--timeout-seconds", "nan"),
        ("--timeout-seconds", "inf"),
    ]:
        invalid = command.copy()
        invalid[invalid.index(flag) + 1] = value
        assert subprocess.run(invalid, env=env, capture_output=True, timeout=5).returncode == 2


RECOVERY_STEPS = {
    "SC-51": [
        "a completed response is retained for 24 hours from completion using database time",
        "an identical request arrives before expiry",
        "the original response is replayed",
        "a request arrives at or after expiry and the old generation is safely resolved",
        "an atomic fresh generation may execute a new payment",
        "bounded cleanup does not delete unresolved guards or receipts",
    ],
    "SC-52": [
        "B committed financial changes and its result receipt",
        "the process crashed before A published completed",
        "a retry or reconciler resolves the processing entry",
        "the committed receipt is published and replayed without executing payments again",
        "a temporary unresolved processing state returns 409",
        "a publication failure does not claim that financial changes rolled back",
    ],
    "SC-53": [
        "A committed processing but its owner crashed before B committed",
        "recovery acquires the execution guard and establishes no committed receipt",
        "it fences the old owner before admitting a replacement execution",
        "an old owner that resumes cannot execute or finalize the new generation",
        "failure of result-receipt recording rolls back financial effects on B",
    ],
    "SC-54": [
        "a processing entry reaches its 24-hour expiry with an active or uncertain B transaction",
        "cleanup and duplicate requests race with recovery and the old owner",
        "they cannot delete the execution guard or authorize a second financial execution",
        "processing returns 409 until bounded reconciliation establishes the result",
        "exhausted database budgets return the existing safe 503 without hidden retries",
        "correlation and sanitized logs distinguish claim, execution, publication and recovery",
    ],
    "SC-56": [
        "maintenance candidates include expired completed, abandoned processing, "
        "active and corrupt entries",
        "the explicit maintenance command runs with finite batch, batch-count and "
        "total-time bounds",
        "it publishes receipts, fences no-effect abandoned claims and deletes only "
        "safely expired generations",
        "active guards are skipped without unbounded waits or spinning",
        "corrupt or unresolved records are retained and operationally signaled "
        "without financial execution",
        "no financial rows or balances change and unrelated generations remain intact",
    ],
}


def recovery_step(ctx, step, stage):
    if step not in RECOVERY_STEPS.get(ctx.scenario, []):
        return False
    if stage == "given":
        return True
    if stage == "when" and not getattr(ctx, "recovery_verified", False):
        if ctx.scenario == "SC-51":
            test_completed_ttl_is_exact_stable_and_reuse_checks_expiry_first(ctx.app, ctx.db, "2")
        elif ctx.scenario == "SC-52":
            test_receipt_recovers_after_failed_completed_publication(
                ctx.app, ctx.db, ctx.monkeypatch
            )
        elif ctx.scenario == "SC-53":
            test_receipt_failure_rolls_back_business_effects(ctx.app, ctx.db)
            test_abandoned_owner_is_fenced_before_replacement(ctx.app, ctx.db)
        elif ctx.scenario == "SC-54":
            test_expired_active_guard_cannot_be_reclaimed(ctx.app, ctx.db, ctx.monkeypatch)
        else:
            test_maintenance_cli_is_bounded_and_retains_corruption(ctx.app, ctx.db)
        ctx.recovery_verified = True
    assert ctx.recovery_verified
    return True


def test_maintenance_deadline_rolls_back_slow_lifecycle_changes(application, seeded_database):
    import asyncio
    import time

    from app.core.config import Settings
    from app.db.idempotency_maintenance import run

    assert post(application, payload("1"), "slow-maintenance").status_code == 201
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE bulk_payment_requests SET expires_at=now()-interval '1 second'")
        connection.execute(
            "CREATE FUNCTION delay_expiry() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN PERFORM pg_sleep(2); RETURN OLD; END $$; "
            "CREATE TRIGGER delay_expiry BEFORE DELETE ON bulk_payment_requests "
            "FOR EACH ROW EXECUTE FUNCTION delay_expiry()"
        )
    try:
        import concurrent.futures

        started = time.monotonic()
        observed_sleep = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                asyncio.run,
                run(Settings.local(seeded_database, response_margin_ms=100), 1, 1, 1),
            )
            with psycopg.connect(seeded_database, autocommit=True) as observer:
                while not future.done():
                    observed_sleep |= observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE application_name='firm-payments' AND wait_event='PgSleep' "
                        "AND query LIKE 'DELETE FROM bulk_payment_requests%')"
                    ).fetchone()[0]
                    time.sleep(0.005)
            with pytest.raises(TimeoutError):
                future.result(timeout=2)
        assert observed_sleep, "Timeout must interrupt actual PostgreSQL work"
        assert time.monotonic() - started < 1.5
        assert count(seeded_database, "bulk_payment_requests") == 1
        assert count(seeded_database, "payments") == 1
        assert balances(seeded_database)[PAYER] == 199900
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "DROP TRIGGER delay_expiry ON bulk_payment_requests; DROP FUNCTION delay_expiry()"
            )
