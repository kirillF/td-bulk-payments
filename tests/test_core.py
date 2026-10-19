"""Atomic API response oracles, including the exact operator's SC-21 sample."""

import json
from pathlib import Path

import psycopg
import pytest

from .conftest import OTHER, PAYEE, PAYER, balances, count, payload, post


def post_escaped_json(app, body, key):
    return app.state.test_client.post(
        "/v1/bulk-payments",
        content=json.dumps(body, ensure_ascii=True),
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
    )


@pytest.mark.parametrize(
    "field", ["payer_firm_uuid", "payee_firm_uuid", "description", "extra_key"]
)
def test_invalid_surrogate_input_has_safe_correlated_422(application, seeded_database, field):
    body = payload()
    if field == "extra_key":
        body["\ud800"] = "unknown field"
    elif field == "payer_firm_uuid":
        body[field] = "\ud800"
    else:
        body["payments"][0][field] = {"text": "\ud800"} if field == "description" else "\ud800"
    response = post_escaped_json(application, body, "invalid-unicode")
    assert response.status_code == 422
    assert response.headers["X-Request-ID"]
    assert isinstance(response.json()["detail"], list)
    assert all(set(e) == {"loc", "msg", "type"} for e in response.json()["detail"])
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert (
        count(seeded_database, "payments") == count(seeded_database, "bulk_payment_requests") == 0
    )


@pytest.mark.parametrize("dirty_first", [True, False])
@pytest.mark.parametrize(
    ("raw", "clean"),
    [("a\x00b", "ab"), ("\x00", ""), ("a\ud800b\udfff", "ab"), ("😀\x00 \té", "😀 \té")],
)
def test_description_sanitization_is_durable_and_idempotent(
    application, seeded_database, raw, clean, dirty_first
):
    dirty, sanitized = payload("1"), payload("1")
    dirty["payments"][0]["description"] = raw
    sanitized["payments"][0]["description"] = clean
    first_body, second_body = (dirty, sanitized) if dirty_first else (sanitized, dirty)
    first = post_escaped_json(application, first_body, "sanitize")
    second = post_escaped_json(application, second_body, "sanitize")
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert first.json()["payments"][0]["description"] == clean
    with psycopg.connect(seeded_database) as connection:
        assert connection.execute("SELECT description FROM payments").fetchall() == [(clean,)]
        stored = connection.execute("SELECT response_body FROM bulk_payment_requests").fetchone()[0]
        assert stored == first.json()
    assert (
        count(seeded_database, "payments") == count(seeded_database, "bulk_payment_requests") == 1
    )
    assert balances(seeded_database) == {PAYER: 199900, PAYEE: 100, OTHER: 0}


def test_sc21_exact_sample(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("TRUNCATE payments, bulk_payment_requests, firms RESTART IDENTITY")
        connection.execute((Path(__file__).resolve().parents[1] / "examples/firms.sql").read_text())
    body = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/bulk-payment.json").read_text()
    )
    result = post(application, body)
    assert result.status_code == 201
    response = result.json()
    assert response["payments_created"] == 3
    assert response["total_amount_cents"] == 1325125
    assert balances(seeded_database) == {PAYER: 3674875, PAYEE: 170075, OTHER: 1405050}
    assert sum(balances(seeded_database).values()) == 5250000
    with psycopg.connect(seeded_database) as connection:
        rows = connection.execute(
            "SELECT id,payer_firm_id,payee_firm_id,amount_cents,description "
            "FROM payments ORDER BY id"
        ).fetchall()
    assert [tuple(row[1:]) for row in rows] == [
        (1, 3, 625000, "Overflow returns, August 2026"),
        (1, 3, 580050, "Amended returns, August 2026"),
        (1, 2, 120075, "Bookkeeping cleanup, 3 clients"),
    ]
    assert response["payments"] == [
        {
            "id": row[0],
            "payer_firm_uuid": PAYER,
            "payee_firm_uuid": payment["payee_firm_uuid"],
            "amount_cents": row[3],
            "description": row[4],
        }
        for row, payment in zip(rows, body["payments"], strict=True)
    ]


def test_returned_ids_are_actual_sequence_values(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        # PostgreSQL sequences need not be contiguous or start at one.
        connection.execute("SELECT setval(pg_get_serial_sequence('payments','id'),1000)")
    result = post(application, payload("10"))
    assert result.status_code == 201
    assert result.json()["payments"][0]["id"] == 1001
    with psycopg.connect(seeded_database) as connection:
        assert connection.execute("SELECT id FROM payments").fetchone() == (1001,)
    assert count(seeded_database, "payments") == 1


def test_within_batch_id_gaps_preserve_order_and_replay(application, seeded_database):
    with psycopg.connect(seeded_database) as connection:
        connection.execute("""
            CREATE FUNCTION consume_payment_id() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM nextval(pg_get_serial_sequence('payments','id')); RETURN NEW; END $$;
            CREATE TRIGGER consume_payment_id BEFORE INSERT ON payments
            FOR EACH ROW EXECUTE FUNCTION consume_payment_id();
        """)
    body = payload("1")
    body["payments"] += [
        {**body["payments"][0], "amount": "2", "description": "second"},
        {**body["payments"][0], "amount": "3", "description": "third", "payee_firm_uuid": OTHER},
    ]
    try:
        first = post(application, body)
        assert first.status_code == 201
        with psycopg.connect(seeded_database) as connection:
            rows = connection.execute(
                "SELECT id,amount_cents,description FROM payments ORDER BY id"
            ).fetchall()
        assert [r[0] for r in rows] == [1, 3, 5]
        assert [
            (p["id"], p["amount_cents"], p["description"]) for p in first.json()["payments"]
        ] == rows
        assert [p["payee_firm_uuid"] for p in first.json()["payments"]] == [PAYEE, PAYEE, OTHER]
        assert post(application, body).json() == first.json()
        assert count(seeded_database, "payments") == 3
    finally:
        with psycopg.connect(seeded_database) as connection:
            connection.execute(
                "DROP TRIGGER consume_payment_id ON payments; DROP FUNCTION consume_payment_id()"
            )


@pytest.mark.parametrize("values", [["same", "same"], ["first", "second"]])
def test_duplicate_key_headers_rejected_before_reservation(application, seeded_database, values):
    result = application.state.test_client.post(
        "/v1/bulk-payments",
        json=payload(),
        headers=[("Idempotency-Key", values[0]), ("iDeMpOtEnCy-KeY", values[1])],
    )
    assert result.status_code == 422
    assert isinstance(result.json()["detail"], list)
    assert result.json()["detail"][0]["loc"] == ["header", "Idempotency-Key"]
    assert result.headers["X-Request-ID"]
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert (
        count(seeded_database, "payments") == count(seeded_database, "bulk_payment_requests") == 0
    )


def test_openapi_documents_all_payment_responses(application):
    schema = application.openapi()
    responses = schema["paths"]["/v1/bulk-payments"]["post"]["responses"]
    assert {"201", "422", "409", "503"} <= responses.keys()
    choices = responses["422"]["content"]["application/json"]["schema"]["anyOf"]
    assert {c["$ref"].rsplit("/", 1)[1] for c in choices} == {"ErrorResponse", "ValidationResponse"}
    assert responses["503"]["headers"]["Retry-After"]["schema"]["type"] == "string"


@pytest.mark.parametrize(
    ("amount", "status", "cents"),
    [
        ("0.01", 201, 1),
        ("1.2", 201, 120),
        ("0001.00", 201, 100),
        ("0", 422, None),
        ("-1", 422, None),
        ("+1", 422, None),
        ("1.001", 422, None),
        ("1e2", 422, None),
        (".1", 422, None),
        ("1.", 422, None),
        (" 1", 422, None),
        (1, 422, None),
        ("21474836.48", 422, None),
    ],
)
def test_sc5_http_amount_rows(application, seeded_database, amount, status, cents):
    response = post(application, payload(amount))
    assert response.status_code == status
    if cents is None:
        assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
        assert count(seeded_database, "payments") == 0
        assert count(seeded_database, "bulk_payment_requests") == 0
    else:
        assert response.json()["payments"][0]["amount_cents"] == cents
        assert balances(seeded_database) == {PAYER: 200000 - cents, PAYEE: cents, OTHER: 0}


def test_known_then_unknown_payee_is_atomic(application, seeded_database):
    body = payload("10")
    body["payments"].append(
        {**body["payments"][0], "payee_firm_uuid": "00000000-0000-0000-0000-000000000000"}
    )
    assert post(application, body).status_code == 422
    assert balances(seeded_database) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    assert count(seeded_database, "payments") == 0


def test_bootstrap_loads_dotenv_without_environment_database_url(seeded_database, tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "app", tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(root / "alembic.ini", tmp_path / "alembic.ini")
    shutil.copytree(
        root / "migrations", tmp_path / "migrations", ignore=shutil.ignore_patterns("__pycache__")
    )
    (tmp_path / ".env").write_text(f"DATABASE_URL={seeded_database}\nPAYMENTS_PROFILE=local\n")
    before = balances(seeded_database)
    environment = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    subprocess.run(
        [sys.executable, "-m", "app.db.migrate"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert balances(seeded_database) == before
    assert (
        count(seeded_database, "payments") == count(seeded_database, "bulk_payment_requests") == 0
    )
