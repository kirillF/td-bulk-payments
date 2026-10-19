import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from app.db.migrate import migrate
from app.schemas.payments import MAX_CENTS

from .conftest import OTHER, PAYEE, PAYER, balances, borrowed, count, payload, post, running_app

pytestmark = pytest.mark.integration


def wait_for_blocked_requests(database_url, expected):
    deadline = time.monotonic() + 4
    with psycopg.connect(database_url, autocommit=True) as connection:
        while time.monotonic() < deadline:
            blocked = connection.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE application_name = 'firm-payments' AND wait_event_type = 'Lock'"
            ).fetchone()[0]
            if blocked >= expected:
                return
            time.sleep(0.01)
    pytest.fail(f"did not observe {expected} overlapping requests blocked in PostgreSQL")


def test_example_bulk(application, seeded_database):
    body = payload("1200.75")
    body["payments"][0]["description"] = "Bookkeeping cleanup, 3 clients"
    body["payments"].append(
        {"amount": "300", "payee_firm_uuid": OTHER, "description": "Referral fee, 2 clients"}
    )
    result = post(application, body)
    assert result.status_code == 201
    assert result.json()["payments_created"] == 2
    assert result.json()["total_amount_cents"] == 150075
    with psycopg.connect(seeded_database) as connection:
        ids = [row[0] for row in connection.execute("SELECT id FROM payments ORDER BY id")]
    assert result.json()["payments"] == [
        {
            "id": ids[0],
            "payer_firm_uuid": PAYER,
            "payee_firm_uuid": PAYEE,
            "amount_cents": 120075,
            "description": "Bookkeeping cleanup, 3 clients",
        },
        {
            "id": ids[1],
            "payer_firm_uuid": PAYER,
            "payee_firm_uuid": OTHER,
            "amount_cents": 30000,
            "description": "Referral fee, 2 clients",
        },
    ]
    assert balances(seeded_database) == {PAYER: 49925, PAYEE: 120075, OTHER: 30000}
    with psycopg.connect(seeded_database) as connection:
        rows = connection.execute(
            "SELECT payer_firm_id, payee_firm_id, amount_cents, description "
            "FROM payments ORDER BY id"
        ).fetchall()
        assert rows == [
            (1, 2, 120075, "Bookkeeping cleanup, 3 clients"),
            (1, 3, 30000, "Referral fee, 2 clients"),
        ]


def test_exact_balance_and_repeated_payee(application, seeded_database):
    body = payload("1000")
    body["payments"] *= 2
    assert post(application, body).status_code == 201
    assert balances(seeded_database) == {PAYER: 0, PAYEE: 200000, OTHER: 0}
    assert count(seeded_database, "payments") == 2


def test_insufficient_total_leaves_everything_unchanged(application, seeded_database):
    body = payload("1200")
    body["payments"].append({"amount": "900", "payee_firm_uuid": OTHER, "description": ""})
    assert post(application, body).status_code == 422
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert count(seeded_database, "payments") == 0
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE firms SET balance_cents = 300000 WHERE uuid = %s", (PAYER,))
    # A declined key remains declined even if money arrives later.
    assert post(application, body).status_code == 422
    assert post(application, body, "new-attempt").status_code == 201


@pytest.mark.parametrize("missing", ["payer", "payee"])
def test_unknown_firm_is_atomic(application, seeded_database, missing):
    body = payload(payer=str(uuid4())) if missing == "payer" else payload(payee=str(uuid4()))
    assert post(application, body).status_code == 422
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert count(seeded_database, "payments") == 0


@pytest.mark.parametrize("key", [None, "", "with space", "a" * 129])
def test_invalid_header(application, seeded_database, key):
    assert post(application, payload(), key).status_code == 422
    assert count(seeded_database, "bulk_payment_requests") == 0


@pytest.mark.parametrize(
    "body",
    [
        {"payer_firm_uuid": PAYER, "payments": []},
        payload(payee=PAYER),
        payload(payer="invalid"),
        payload(payee="invalid"),
        {"payer_firm_uuid": PAYER, "payments": [{"amount": "1", "payee_firm_uuid": PAYEE}]},
        {
            "payer_firm_uuid": PAYER,
            "payments": [{**payload()["payments"][0], "description": "x" * 2001}],
        },
        payload("0.001"),
        payload(10),
        {**payload(), "extra": "unexpected"},
        {"payer_firm_uuid": PAYER, "payments": payload()["payments"] * 1001},
    ],
)
def test_invalid_request_does_not_reserve_key(application, seeded_database, body):
    assert post(application, body).status_code == 422
    assert count(seeded_database, "payments") == 0
    assert count(seeded_database, "bulk_payment_requests") == 0


def test_recipient_overflow_is_atomic(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute(
            "UPDATE firms SET balance_cents = %s WHERE uuid = %s", (MAX_CENTS - 1, PAYEE)
        )
    before = balances(seeded_database)
    body = payload("0.01")
    body["payments"] *= 2
    assert post(application, body).json() == {"detail": "balance_limit_exceeded"}
    assert balances(seeded_database) == before
    assert count(seeded_database, "payments") == 0


def test_replay_and_conflict(application, seeded_database):
    first = post(application, payload("01.0"))
    replay = post(application, payload("1.00", payer=PAYER.upper()))
    assert (first.status_code, first.json()) == (replay.status_code, replay.json())
    assert first.status_code == 201
    assert first.json()["payments_created"] == 1
    assert first.json()["payments"][0]["amount_cents"] == 100
    assert post(application, payload("2")).status_code == 409
    changed = payload("1")
    changed["payments"][0]["description"] = "Changed"
    assert post(application, changed).status_code == 409
    assert count(seeded_database, "payments") == 1
    assert balances(seeded_database) == {PAYER: 199900, PAYEE: 100, OTHER: 0}


def test_sql_description_is_data(application, seeded_database):
    body = payload()
    text = "'); DROP TABLE firms; -- café 🧾"
    body["payments"][0]["description"] = text
    assert post(application, body).status_code == 201
    with psycopg.connect(seeded_database) as connection:
        assert connection.execute("SELECT description FROM payments").fetchone()[0] == text


def test_database_failure_rolls_back_then_same_key_can_retry(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("""
            CREATE FUNCTION fail_test_payment() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected payment insert failure'; END; $$;
            CREATE TRIGGER fail_test_payment BEFORE INSERT ON payments
            FOR EACH ROW EXECUTE FUNCTION fail_test_payment();
        """)
    try:
        result = post(application, payload())
        assert result.status_code == 503
        assert result.headers["retry-after"] == "1"
        assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
        assert count(seeded_database, "payments") == 0
        assert count(seeded_database, "bulk_payment_requests") == 0
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "DROP TRIGGER fail_test_payment ON payments; DROP FUNCTION fail_test_payment()"
            )
    assert post(application, payload()).status_code == 201


def test_lost_http_response_replays_after_commit(application, seeded_database, monkeypatch):
    from .test_observability import wait_events

    logs = []
    application.state.telemetry.sink = logs.append
    module = importlib.import_module("app.api.v1.payments")
    response_class = module.JSONResponse

    def lost_response(*args, **kwargs):
        raise RuntimeError("response lost after database commit")

    monkeypatch.setattr(module, "JSONResponse", lost_response)
    failure = post(application, payload(), raise_exceptions=False)
    assert failure.status_code == 500
    event = wait_events(logs, 1)[0]
    assert event["request_id"] == failure.headers["X-Request-ID"]
    assert event["status_code"] == 500
    assert event["outcome"] == "committed"
    monkeypatch.setattr(module, "JSONResponse", response_class)
    assert post(application, payload()).status_code == 201
    assert count(seeded_database, "payments") == 1


def test_parallel_idempotency_and_spending_across_instances(application, seeded_database):
    manager = running_app(seeded_database)
    second = manager.__enter__()
    try:
        assert application.state.pool is not second.state.pool
        for identical in [True, False]:
            with psycopg.connect(seeded_database) as connection:
                connection.execute(
                    "TRUNCATE request_result_receipts, request_execution_guards, "
                    "payments, bulk_payment_requests"
                )
                connection.execute(
                    "UPDATE firms SET balance_cents = CASE WHEN uuid = %s THEN 10000 ELSE 0 END",
                    (PAYER,),
                )
            barrier = Barrier(8)

            def submit(index, barrier=barrier, identical=identical):
                barrier.wait(timeout=10)
                app = application if index % 2 else second
                return post(app, payload("30"), "same" if identical else f"key-{index}")

            with psycopg.connect(seeded_database) as blocker:
                blocker.execute("SELECT id FROM firms WHERE uuid = %s FOR UPDATE", (PAYER,))
                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(submit, i) for i in range(8)]
                    try:
                        wait_for_blocked_requests(seeded_database, 1 if identical else 8)
                    finally:
                        blocker.commit()
                    results = [future.result(timeout=15) for future in futures]
            statuses = [r.status_code for r in results]
            if identical:
                assert statuses.count(201) == 1 and statuses.count(409) == 7
                assert all(
                    r.json() == {"detail": "request_processing"}
                    for r in results
                    if r.status_code == 409
                )
                replay = post(application, payload("30"), "same")
                assert replay.json() == next(r.json() for r in results if r.status_code == 201)
                assert count(seeded_database, "payments") == 1
                assert balances(seeded_database) == {PAYER: 7000, PAYEE: 3000, OTHER: 0}
            else:
                assert statuses.count(201) == 3
                assert statuses.count(422) == 5
                assert count(seeded_database, "payments") == 3
                assert balances(seeded_database) == {PAYER: 1000, PAYEE: 9000, OTHER: 0}
    finally:
        manager.__exit__(None, None, None)


def test_opposing_transfers_do_not_deadlock(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("UPDATE firms SET balance_cents = 10000")
    barrier = Barrier(3)
    firms = [PAYER, PAYEE, OTHER]

    def submit(index):
        barrier.wait(timeout=10)
        return post(application, payload("100", payer=firms[index], payee=firms[(index + 1) % 3]))

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(submit, range(3)))
    assert [r.status_code for r in results] == [201] * 3
    assert balances(seeded_database) == {PAYER: 10000, PAYEE: 10000, OTHER: 10000}
    assert count(seeded_database, "payments") == 3


def test_lock_timeout_rolls_back_reservation_and_allows_retry(application, seeded_database):
    with borrowed(application) as connection:
        connection.execute("SET lock_timeout = '100ms'")
    with psycopg.connect(seeded_database) as blocker:
        blocker.execute("SELECT id FROM firms WHERE uuid = %s FOR UPDATE", (PAYER,))
        assert post(application, payload()).status_code == 503
        assert count(seeded_database, "payments") == 0
        assert count(seeded_database, "bulk_payment_requests") == 0
    assert post(application, payload()).status_code == 201


def test_uuid_lookup_uses_unique_index_under_load(seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("""INSERT INTO firms(name, uuid, balance_cents)
            SELECT 'Firm ' || n, md5(n::text)::uuid::text, 0 FROM generate_series(1, 10000) AS n""")
        connection.execute("ANALYZE firms")
        plan = connection.execute(
            "EXPLAIN (FORMAT JSON) SELECT id FROM firms WHERE uuid = ANY(%s::text[])",
            ([PAYER, PAYEE],),
        ).fetchone()[0]
        assert "firms_uuid_idx" in str(plan)
        index = connection.execute(
            "SELECT indisunique FROM pg_index WHERE indexrelid = 'firms_uuid_idx'::regclass"
        ).fetchone()
        assert index == (True,)


def test_schema_bootstrap_is_repeatable(seeded_database):
    migrate(seeded_database)
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
