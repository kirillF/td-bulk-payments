"""SC-26: clean Linux build, Compose bootstrap ordering and non-mutating HTTP startup."""

import json
import os
import shutil
import subprocess
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def command(argv, *, timeout=60):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=timeout).stdout


@contextmanager
def container_service():
    identifier = uuid.uuid4().hex
    parent = Path(os.environ.get("PAYMENTS_CHECK_RUN_DIR", ROOT / ".harness/container-checks"))
    run = parent / "containers" / identifier
    context = run / "context"
    context.mkdir(parents=True)
    for name in ["Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "alembic.ini"]:
        shutil.copy2(ROOT / name, context / name)
    shutil.copytree(
        ROOT / "app",
        context / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        ROOT / "migrations",
        context / "migrations",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    assert not (context / ".venv").exists()
    assert not (context / ".env").exists()
    original = json.loads(
        command(
            ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "config", "--format", "json"]
        )
    )
    services = original["services"]
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert (
        services["service"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )
    project = "td-bulk-test-" + identifier
    image = "td-bulk-payments-test:" + identifier
    for name in ["migrate", "service"]:
        services[name]["build"] = {
            "context": str(context),
            "labels": {"td-bulk-payments.run": identifier},
        }
        services[name]["image"] = image
        services[name]["mem_limit"] = "256m"
        services[name]["cpus"] = 1
        services[name]["pids_limit"] = 128
    for name, port in [("postgres", 5432), ("service", 8000)]:
        services[name]["ports"] = [
            {"target": port, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}
        ]
    original.pop("name", None)
    # Compose config resolves volume names to the original project: replace that name.
    for volume in original.get("volumes", {}).values():
        volume.pop("name", None)
    config = run / "compose.json"
    config.write_text(json.dumps(original))
    (run / "ownership.json").write_text(json.dumps({"project": project, "image": image}))
    base = ["docker", "compose", "-p", project, "-f", str(config)]
    try:
        command(base + ["build"], timeout=180)
        command(base + ["up", "-d"], timeout=60)
        pg_port = command(base + ["port", "postgres", "5432"]).strip().rsplit(":", 1)[1]
        http_port = command(base + ["port", "service", "8000"]).strip().rsplit(":", 1)[1]
        url = f"postgresql://firm_payments:local_only@127.0.0.1:{pg_port}/firm_payments"
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{http_port}/openapi.json", timeout=1
                ) as response:
                    assert response.status == 200
                break
            except OSError:
                assert time.monotonic() < deadline, "HTTP application shell did not start"
                time.sleep(0.1)
        runtime = json.loads(
            command(
                base
                + [
                    "exec",
                    "-T",
                    "service",
                    "/app/.venv/bin/python",
                    "-c",
                    "import alembic,dotenv,json,os,sys; "
                    'print(json.dumps({"python":list(sys.version_info[:2]),"uid":os.getuid()}))',
                ]
            )
        )
        assert runtime == {"python": [3, 15], "uid": 10001}
        image_info = json.loads(command(["docker", "image", "inspect", image]))[0]
        (run / "runtime.json").write_text(
            json.dumps({"image_id": image_info["Id"], "runtime": runtime})
        )
        yield {
            "database_url": url,
            "http_url": f"http://127.0.0.1:{http_port}",
            "runtime": runtime,
            "base": base,
            "run": run,
            "image": image,
        }
    finally:
        command(base + ["down", "--volumes", "--remove-orphans"], timeout=25)


def test_sc26_clean_image_and_repeat_safe_bootstrap():
    with container_service() as service:
        url = service["database_url"]
        base = service["base"]
        runtime = service["runtime"]
        run = service["run"]
        image = service["image"]
        with psycopg.connect(url) as connection:
            assert connection.execute("SELECT count(*) FROM payments").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM bulk_payment_requests").fetchone() == (
                0,
            )
            connection.execute(
                "INSERT INTO firms(id,name,balance_cents,uuid) "
                "VALUES (1,'seed',76543,'3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41')"
            )
        command(base + ["run", "--rm", "--no-deps", "migrate"], timeout=40)
        command(base + ["restart", "service"], timeout=20)
        with psycopg.connect(url) as connection:
            assert connection.execute("SELECT id,name,balance_cents FROM firms").fetchall() == [
                (1, "seed", 76543)
            ]
            assert connection.execute("SELECT count(*) FROM payments").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM bulk_payment_requests").fetchone() == (
                0,
            )
        image_info = json.loads(command(["docker", "image", "inspect", image]))[0]
        (run / "runtime.json").write_text(
            json.dumps(
                {
                    "image_id": image_info["Id"],
                    "runtime": runtime,
                    "scenario": "SC-26",
                    "bootstrap_preserved_seed": True,
                }
            )
        )


def test_final_http_samples():
    import httpx

    from .conftest import OTHER, PAYEE, PAYER, payload

    with container_service() as service:
        url = service["database_url"]
        endpoint = service["http_url"] + "/v1/bulk-payments"
        for exact in [False, True]:
            with psycopg.connect(url) as connection:
                connection.execute(
                    "TRUNCATE request_result_receipts,request_execution_guards,"
                    "payments,bulk_payment_requests,firms RESTART IDENTITY"
                )
                if exact:
                    connection.execute((ROOT / "examples/firms.sql").read_text())
                else:
                    connection.execute(
                        "INSERT INTO firms(id,name,uuid,balance_cents) "
                        "VALUES (1,'A',%s,200000),(2,'B',%s,0),(3,'C',%s,0)",
                        (PAYER, PAYEE, OTHER),
                    )
            body = (
                json.loads((ROOT / "examples/bulk-payment.json").read_text())
                if exact
                else payload("1200.75")
            )
            if not exact:
                body["payments"].append(
                    {"amount": "300", "payee_firm_uuid": OTHER, "description": "Referral"}
                )
            headers = {"Idempotency-Key": "http-sample"}
            result = httpx.post(endpoint, json=body, headers=headers, timeout=10)
            assert result.status_code == 201
            with psycopg.connect(url) as connection:
                rows = connection.execute(
                    "SELECT id,payee_firm_id,amount_cents,description FROM payments ORDER BY id"
                ).fetchall()
                expected = [3674875, 170075, 1405050] if exact else [49925, 120075, 30000]
                assert [
                    r[0] for r in connection.execute("SELECT balance_cents FROM firms ORDER BY id")
                ] == expected
            assert result.json()["payments"] == [
                {
                    "id": row[0],
                    "payer_firm_uuid": PAYER,
                    "payee_firm_uuid": entry["payee_firm_uuid"],
                    "amount_cents": row[2],
                    "description": row[3],
                }
                for row, entry in zip(rows, body["payments"], strict=True)
            ]
            assert (
                httpx.post(endpoint, json=body, headers=headers, timeout=10).json() == result.json()
            )
            with psycopg.connect(url) as connection:
                assert connection.execute("SELECT count(*) FROM payments").fetchone() == (
                    len(body["payments"]),
                )
            assert_container_http_lifecycle(url, endpoint)


def assert_container_http_lifecycle(database_url, endpoint):
    """Prove real packaged HTTP metadata/replay/TTL without changing sample oracles."""
    from datetime import timedelta

    import httpx

    from .conftest import PAYEE, PAYER, balances, payload

    before = balances(database_url)
    with psycopg.connect(database_url) as connection:
        count_before = connection.execute("SELECT count(*) FROM payments").fetchone()[0]

    def send(correlation):
        response = httpx.post(
            endpoint,
            json=payload("1"),
            headers={"Idempotency-Key": "http-ttl", "X-Correlation-ID": correlation},
            timeout=10,
        )
        assert response.status_code == 201
        assert response.headers.get_list("X-Correlation-ID") == [correlation]
        assert len(response.headers.get_list("X-Request-ID")) == 1
        assert response.headers["X-Request-ID"]
        return response

    def generation():
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                "SELECT generation,expires_at,completed_at FROM bulk_payment_requests "
                "WHERE payer_firm_uuid=%s AND idempotency_key='http-ttl'",
                (PAYER,),
            ).fetchone()
        assert row and row[1] - row[2] == timedelta(hours=24)
        return row

    initial = send("linux.initial")
    original = generation()
    replay = send("linux.replay")
    assert replay.json() == initial.json() and generation() == original
    after_one = dict(before)
    after_one[PAYER] -= 100
    after_one[PAYEE] += 100
    assert balances(database_url) == after_one
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM payments").fetchone()[0] == count_before + 1
        assert (
            connection.execute(
                "UPDATE bulk_payment_requests SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE generation=%s AND state='completed'",
                (original[0],),
            ).rowcount
            == 1
        )
    replacement = send("linux.expired")
    new = generation()
    assert new[0] != original[0]
    assert replacement.json()["payments"][0]["id"] != initial.json()["payments"][0]["id"]
    replay_new = send("linux.replay-new")
    assert replay_new.json() == replacement.json() and generation() == new
    assert len({r.headers["X-Request-ID"] for r in [initial, replay, replacement, replay_new]}) == 4
    after_two = dict(before)
    after_two[PAYER] -= 200
    after_two[PAYEE] += 200
    assert balances(database_url) == after_two
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM payments").fetchone()[0] == count_before + 2
        for table in ["request_execution_guards", "request_result_receipts"]:
            assert connection.execute(
                f"SELECT count(*) FROM {table} WHERE generation=%s", (original[0],)
            ).fetchone() == (0,)
            assert connection.execute(
                f"SELECT count(*) FROM {table} WHERE generation=%s", (new[0],)
            ).fetchone() == (1,)
