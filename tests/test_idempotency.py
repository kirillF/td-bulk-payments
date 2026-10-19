"""Durable replay/conflict and real committed-result recovery boundaries."""

import json

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
        if body is None:
            connection.execute(
                "INSERT INTO bulk_payment_requests "
                "(payer_firm_uuid,idempotency_key,request_fingerprint) VALUES (%s,%s,%s)",
                (PAYER, "test-key", request.fingerprint()),
            )
        else:
            connection.execute(
                "INSERT INTO bulk_payment_requests "
                "(payer_firm_uuid,idempotency_key,request_fingerprint,status_code,response_body) "
                "VALUES (%s,%s,%s,201,%s::jsonb)",
                (PAYER, "test-key", request.fingerprint(), json.dumps(body)),
            )
    assert post(application, payload("1")).status_code == 503
    assert count(seeded_database, "payments") == 0
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert count(seeded_database, "bulk_payment_requests") == 1


def test_lost_commit_ack_replays_committed_result(application, seeded_database, monkeypatch):
    original = psycopg.AsyncTransaction.__aexit__
    armed = True

    async def lose_ack(transaction, exc_type, exc_value, traceback):
        nonlocal armed
        result = await original(transaction, exc_type, exc_value, traceback)
        if armed and exc_type is None:
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
