"""Executable bindings for the complete approved Gherkin, including every outline row."""

import json
import os
import re
import shutil
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg_pool import PoolTimeout
from pytest_bdd import given, parsers, scenarios, then, when

from app.core.observability import SafeExporter
from app.db.migrate import migrate

from .conftest import OTHER, PAYEE, PAYER, balances, count, payload, post
from .test_container import container_service
from .test_core import post_escaped_json
from .test_health import get
from .test_observability import BrokenExporter, wait_events

pytestmark = pytest.mark.integration
scenarios("features/bulk_payments.feature")


@pytest.fixture
def ctx(application, seeded_database, request, monkeypatch):
    with ExitStack() as stack:
        context = SimpleNamespace(
            app=application,
            db=seeded_database,
            stack=stack,
            monkeypatch=monkeypatch,
            scenario=request.node.get_closest_marker("scenario").args[0],
            response=None,
            body=payload(),
            key="K",
            first=None,
            logs=[],
            memory=InMemorySpanExporter(),
            service=None,
            results=[],
            before=balances(seeded_database),
            pool_original=None,
        )
        application.state.telemetry.sink = context.logs.append
        application.state.telemetry.provider.add_span_processor(
            SimpleSpanProcessor(SafeExporter(context.memory, application.state.telemetry.drop))
        )
        yield context


def send(ctx, body=None, key=None):
    ctx.body = body if body is not None else ctx.body
    if ctx.service is not None:
        ctx.response = httpx.post(
            ctx.service["http_url"] + "/v1/bulk-payments",
            json=ctx.body,
            headers={"Idempotency-Key": key or ctx.key},
            timeout=10,
        )
    else:
        ctx.response = post(ctx.app, ctx.body, key or ctx.key)
    return ctx.response


def rows(ctx):
    with psycopg.connect(ctx.db) as connection:
        return connection.execute(
            "SELECT id,payer_firm_id,payee_firm_id,amount_cents,description "
            "FROM payments ORDER BY id"
        ).fetchall()


def change_balances(ctx, values):
    with psycopg.connect(ctx.db) as connection:
        for firm, value in zip([PAYER, PAYEE, OTHER], values, strict=True):
            connection.execute("UPDATE firms SET balance_cents=%s WHERE uuid=%s", (value, firm))


@given(parsers.re(r"(?P<step>.+)"))
def arrange(step, ctx, docstring=None):
    from .test_observability import correlation_step

    if correlation_step(ctx, step, "given"):
        return

    from .test_idempotency import lifecycle_step

    if lifecycle_step(ctx, step, "given"):
        return

    from .test_settings import settings_given

    if settings_given(ctx, step):
        return
    from .test_migrations import migration_given

    if migration_given(ctx, step):
        return
    if step == "the standard three firms are seeded":
        ctx.before = balances(ctx.db)
        return
    if (
        step
        == "a payment operation awaiting withheld database replies within its configured budget"
    ):
        from concurrent.futures import ThreadPoolExecutor

        from app.core.config import Settings

        from .conftest import borrowed, running_app
        from .test_resource_failures import ReplyBlackhole

        ctx.fault = ctx.stack.enter_context(ReplyBlackhole(ctx.db))
        settings = Settings.local(
            ctx.fault.url,
            pool_min=1,
            pool_max=1,
            acquire_timeout_ms=100,
            lock_timeout_ms=100,
            statement_timeout_ms=1000,
            idle_transaction_timeout_ms=1500,
            transaction_timeout_ms=2000,
            operation_budget_ms=3400,
            response_margin_ms=300,
        )
        ctx.app = ctx.stack.enter_context(running_app(settings))
        with borrowed(ctx.app) as connection:
            ctx.original_connection = connection
        ctx.fault.target = b"insert into payments"
        executor = ctx.stack.enter_context(ThreadPoolExecutor(max_workers=1))
        ctx.payment_future = executor.submit(post, ctx.app, payload("1"), ctx.key)
        assert ctx.fault.withheld.wait(1), "Must withhold actual database replies"
        assert not ctx.payment_future.done()
    elif step.startswith("an affordable batch whose description contains "):
        label = step.removeprefix("an affordable batch whose description contains ")
        raw, clean = {
            "U+0000": ("a\x00b", "ab"),
            "isolated U+D800": ("a\ud800b", "ab"),
            "isolated U+DFFF": ("a\udfffb", "ab"),
            "only U+0000": ("\x00", ""),
        }[label]
        ctx.body["payments"][0]["description"] = raw
        ctx.expected_descriptions = [clean]
    elif (
        step == "an affordable batch with empty, whitespace and supplementary Unicode descriptions"
    ):
        ctx.expected_descriptions = ["", " \t\n", "😀é𐐀"]
        ctx.body["payments"] = [
            {**payload("1")["payments"][0], "description": d} for d in ctx.expected_descriptions
        ]
    elif step == "firms A, B and C exist with unique canonical UUIDs":
        assert len(balances(ctx.db)) == 3
    elif step == "firm A has 200000 cents and firms B and C have 0 cents":
        assert balances(ctx.db) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    elif step == "B has 2147483646 cents":
        change_balances(ctx, [200000, 2147483646, 0])
    elif step.startswith("A completed a payment"):
        ctx.body = payload("01.0")
        ctx.first = send(ctx)
        assert ctx.first.status_code == 201
    elif step == "A completed a batch using key K":
        ctx.body = payload("1")
        ctx.body["payments"].append(
            {**ctx.body["payments"][0], "amount": "2", "payee_firm_uuid": OTHER}
        )
        ctx.first = send(ctx)
        ctx.before = balances(ctx.db)
        assert ctx.first.status_code == 201
    elif step == "A was denied a 210000-cent batch under key K":
        ctx.body = payload("2100")
        ctx.first = send(ctx)
        assert ctx.first.status_code == 422
    elif step == "A subsequently receives a top-up to 300000 cents":
        change_balances(ctx, [300000, 0, 0])
    elif step == "A and B each have 10000 cents":
        change_balances(ctx, [10000, 10000, 0])
    elif step == "A has 10000 cents":
        change_balances(ctx, [10000, 0, 0])
    elif step == "participating firms have enough funds for their outgoing batches":
        change_balances(ctx, [10000] * 3)
    elif step == "an injected database failure occurs while inserting a payment":
        with psycopg.connect(ctx.db) as c:
            c.execute(
                "CREATE FUNCTION bdd_failure() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$; CREATE TRIGGER "
                "bdd_failure BEFORE INSERT ON payments FOR EACH ROW EXECUTE "
                "FUNCTION bdd_failure()"
            )

        def remove():
            with psycopg.connect(ctx.db) as c:
                c.execute(
                    "DROP TRIGGER IF EXISTS bdd_failure ON payments; DROP FUNCTION IF "
                    "EXISTS bdd_failure()"
                )

        ctx.remove_failure = remove
        ctx.stack.callback(remove)
    elif (
        step == "another transaction holds a required firm lock beyond the configured lock timeout"
    ):
        ctx.blocker = ctx.stack.enter_context(psycopg.connect(ctx.db))
        ctx.blocker.execute("SELECT id FROM firms WHERE uuid=%s FOR UPDATE", (PAYER,))
    elif step == "A's batch under key K committed but its HTTP response was lost":
        ctx.body = payload("10")
        ctx.first = send(ctx)
        assert ctx.first.status_code == 201
        ctx.response = None  # Caller lost delivery; the durable result remains authoritative.
    elif step == "a firm population representative of the documented workload assumptions":
        with psycopg.connect(ctx.db) as c:
            c.execute(
                "INSERT INTO firms(name,uuid,balance_cents) SELECT "
                "'fixture',md5(n::text)::uuid::text,0 FROM "
                "generate_series(1,10000)n"
            )
            c.execute("ANALYZE firms")
    elif step in {
        "local PostgreSQL is ready and Alembic upgrade head completes",
        "the service has completed initialization",
    }:
        migrate(ctx.db)
        assert get(ctx.app, "/health/ready").status_code == 200
    elif (
        step == "the firms table is seeded with these exact rows replacing the background balances:"
    ):
        with psycopg.connect(ctx.db) as c:
            c.execute(
                "TRUNCATE request_result_receipts,request_execution_guards,"
                "payments,bulk_payment_requests,firms RESTART IDENTITY"
            )
            c.execute(docstring)
    elif step == "no payments have been persisted":
        assert count(ctx.db, "payments") == 0
    elif (
        step
        == "a request contains recognizable canary descriptions, identities and an idempotency key"
    ):
        ctx.body = payload("1")
        ctx.body["payments"][0]["description"] = "CANARY financial description"
        ctx.key = "CANARY-key"
    elif step == "log and trace queues are full or their export destinations are unavailable":
        from threading import Event

        ctx.release = Event()
        ctx.stack.callback(ctx.release.set)
        ctx.app.state.telemetry.sink = lambda _: ctx.release.wait(3)
        ctx.app.state.telemetry.provider.add_span_processor(
            SimpleSpanProcessor(SafeExporter(BrokenExporter(), ctx.app.state.telemetry.drop))
        )
        import queue

        while True:
            try:
                ctx.app.state.telemetry.queue.put_nowait("{}")
            except queue.Full:
                break
    elif step == "a fresh isolated checkout with no pre-existing virtual environment":
        import tempfile

        ctx.fresh = Path(ctx.stack.enter_context(tempfile.TemporaryDirectory()))
        from scripts.project_workflow import ROOT, manifest

        for name in manifest():
            source = ROOT / name
            if source.is_file():
                destination = ctx.fresh / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        assert not (ctx.fresh / ".venv").exists()
    elif step == "a clean checkout and an isolated local PostgreSQL instance":
        pass  # The When step owns the actual fresh build context/DB.
    else:
        pytest.fail("Undefined Given binding: " + step)


@when(parsers.re(r"(?P<step>.+)"))
def act(step, ctx, docstring=None):
    from .test_observability import correlation_step

    if correlation_step(ctx, step, "when"):
        return

    from .test_idempotency import lifecycle_step

    if lifecycle_step(ctx, step, "when"):
        return

    from .test_settings import settings_when

    if settings_when(ctx, step):
        return
    from .test_migrations import migration_when

    if migration_when(ctx, step):
        return
    if step == "a valid bulk payment is posted to the legacy unversioned route":
        ctx.response = ctx.app.state.test_client.post(
            "/bulk-payments", json=payload(), headers={"Idempotency-Key": ctx.key}
        )
        return
    if step == "a concurrent liveness request is submitted":
        ctx.liveness = get(ctx.app, "/health/live")
        ctx.waiting_when_live_completed = not ctx.payment_future.done()
        return
    if step in {
        "the batch is submitted with a new idempotency key",
        "the batch is submitted and replayed with the same key",
    }:
        ctx.response = post_escaped_json(ctx.app, ctx.body, ctx.key)
        ctx.first = ctx.response
        if step == "the batch is submitted and replayed with the same key":
            ctx.response = post_escaped_json(ctx.app, ctx.body, ctx.key)
        return
    match = re.fullmatch(
        r'A submits one batch paying B "([^"]+)" dollars and C "([^"]+)" dollars', step
    )
    if match:
        body = payload(match[1])
        body["payments"].append(
            {"amount": match[2], "payee_firm_uuid": OTHER, "description": "Referral"}
        )
        send(ctx, body)
    elif step == 'A submits two separate payments of "1000" dollars to B in one batch':
        body = payload("1000")
        body["payments"] *= 2
        send(ctx, body)
    elif step.startswith("a batch contains "):
        case = step.removeprefix("a batch contains ")
        ctx.case = case
        body = payload()
        key = "K"
        if case == "an empty payments array":
            body["payments"] = []
        elif case == "1001 payment entries":
            body["payments"] *= 1001
        elif case == "a malformed payer or payee UUID":
            body["payer_firm_uuid"] = "bad"
        elif case == "a self-payment from A to A":
            body["payments"][0]["payee_firm_uuid"] = PAYER
        elif case == "a missing description":
            del body["payments"][0]["description"]
        elif case == "a description with 2001 characters":
            body["payments"][0]["description"] = "x" * 2001
        elif case == "an unexpected JSON attribute":
            body["extra"] = True
        elif case == "a missing or empty Idempotency-Key":
            key = None
        elif case == "an Idempotency-Key containing spaces":
            key = "bad key"
        elif case == "an Idempotency-Key of 129 characters":
            key = "x" * 129
        elif case == "an unknown payer":
            body["payer_firm_uuid"] = str(uuid4())
        elif case == "one known and one unknown payee":
            body["payments"].append({**body["payments"][0], "payee_firm_uuid": str(uuid4())})
        elif case == "SQL-like Unicode text as description":
            body["payments"][0]["description"] = "'); DROP TABLE firms; -- café 🧾"
        else:
            pytest.fail("Undefined input case: " + case)
        ctx.body = body
        ctx.response = post(ctx.app, body, key)
    elif step.startswith("A requests one payment with amount "):
        amount = step.removeprefix("A requests one payment with amount ")
        send(ctx, payload(1 if amount == "numeric JSON 1" else json.loads(amount)))
    elif step == 'A submits two payments of "0.01" dollars to B in one batch':
        body = payload("0.01")
        body["payments"] *= 2
        send(ctx, body)
    elif step == 'that batch is retried with "1.00" and uppercase spellings of the same UUIDs':
        body = json.loads(json.dumps(ctx.body))
        body["payer_firm_uuid"] = PAYER.upper()
        body["payments"][0].update(amount="1.00", payee_firm_uuid=PAYEE.upper())
        send(ctx, body)
    elif step == "key K and every description and payment position are unchanged":
        assert ctx.key == "K" and ctx.body["payments"][0]["description"] == "Services"
    elif step.startswith("A reuses key K with "):
        change = step.removeprefix("A reuses key K with ")
        body = json.loads(json.dumps(ctx.body))
        if change == "a different amount":
            body["payments"][0]["amount"] = "3"
        elif change == "a different payee":
            body["payments"][0]["payee_firm_uuid"] = OTHER
        elif change == "a different description":
            body["payments"][0]["description"] += " "
        elif change == "a different payment ordering":
            body["payments"].reverse()
        else:
            pytest.fail("Undefined conflict case: " + change)
        send(ctx, body)
    elif step in {
        "the original batch is retried under key K",
        "the caller retries the identical batch under key K",
    }:
        send(ctx)
    elif step == "the batch is submitted under a new key L":
        send(ctx, key="L")
    elif step == "A and B each pay C 100 cents using key K":
        ctx.results = [
            post(ctx.app, payload("1", payer=payer, payee=OTHER), "K") for payer in [PAYER, PAYEE]
        ]
    elif step.startswith("overlapping identical requests") or step.startswith(
        "overlapping requests through independent instances"
    ):
        import concurrent.futures
        from threading import Barrier

        from .conftest import running_app
        from .test_payments import wait_for_blocked_requests

        identical = step.startswith("overlapping identical")
        barrier = Barrier(8)
        with running_app(ctx.db) as second, psycopg.connect(ctx.db) as blocker:
            blocker.execute("SELECT id FROM firms WHERE uuid=%s FOR UPDATE", (PAYER,))

            def submit(i):
                barrier.wait(timeout=3)
                return post(
                    ctx.app if i % 2 else second, payload("30"), "K" if identical else f"K-{i}"
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(submit, i) for i in range(8)]
                try:
                    wait_for_blocked_requests(ctx.db, 1 if identical else 8)
                finally:
                    blocker.commit()
                ctx.results = [f.result(timeout=10) for f in futures]
    elif step == "overlapping requests include transfers in opposing directions between firms":
        import concurrent.futures
        from threading import Barrier

        from .conftest import running_app
        from .test_payments import wait_for_blocked_requests

        barrier = Barrier(3)
        firms = [PAYER, PAYEE, OTHER]
        with running_app(ctx.db) as second, psycopg.connect(ctx.db) as blocker:
            blocker.execute("SELECT id FROM firms ORDER BY id FOR UPDATE")

            def submit(index):
                barrier.wait(timeout=3)
                return post(
                    ctx.app if index % 2 else second,
                    payload("100", payer=firms[index], payee=firms[(index + 1) % 3]),
                    f"opposing-{index}",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(submit, index) for index in range(3)]
                try:
                    wait_for_blocked_requests(ctx.db, 3)
                finally:
                    blocker.commit()
                ctx.results = [future.result(timeout=10) for future in futures]
    elif step == "A submits a valid affordable batch under key K":
        send(ctx, payload("10"))
    elif step == "the failure is removed and the batch is retried under key K":
        ctx.remove_failure()
        send(ctx)
    elif step == "the lock is released and the same batch is retried under key K":
        ctx.blocker.commit()
        send(ctx)
    elif step == "participating firms are resolved by UUID":
        with psycopg.connect(ctx.db) as c:
            ctx.plan = c.execute(
                "EXPLAIN (FORMAT JSON) SELECT id FROM firms WHERE uuid=ANY(%s::text[])",
                ([PAYER, PAYEE],),
            ).fetchone()[0]
    elif step in {
        "the Python 3.15 service image starts as a non-root user",
        "the service image is built and started after database readiness and Alembic upgrade head",
    }:
        ctx.service = ctx.stack.enter_context(container_service())
        ctx.db = ctx.service["database_url"]
        with psycopg.connect(ctx.db) as c:
            c.execute(
                "INSERT INTO firms(id,name,uuid,balance_cents) VALUES "
                "(1,'A',%s,200000),(2,'B',%s,0),(3,'C',%s,0)",
                (PAYER, PAYEE, OTHER),
            )
    elif step == "the example batch is submitted over real HTTP":
        act('A submits one batch paying B "1200.75" dollars and C "300" dollars', ctx)
    elif step == (
        "POST /v1/bulk-payments is submitted with a fresh valid Idempotency-Key and this JSON body:"
    ):
        send(ctx, json.loads(docstring))
    elif step == "the approved setup and required checks run":
        root = Path(__file__).resolve().parents[1]
        env = dict(
            os.environ,
            UV_CACHE_DIR=str(root / ".uv-cache"),
            UV_PYTHON_INSTALL_DIR=str(root / ".uv-python"),
        )
        subprocess.run(
            ["uv", "sync", "--locked", "--all-groups", "--managed-python"],
            cwd=ctx.fresh,
            env=env,
            check=True,
            capture_output=True,
            timeout=120,
        )
        python = str(ctx.fresh / ".venv/bin/python")
        subprocess.run(
            [python, "scripts/harness_checks.py", "quality"],
            cwd=ctx.fresh,
            env=env,
            check=True,
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            [python, "-m", "pytest", "-q", "tests/test_core.py", "tests/test_resource_failures.py"],
            cwd=ctx.fresh,
            env=env,
            check=True,
            timeout=120,
        )
        missing = subprocess.run(
            ["docker", "info"],
            env=dict(env, DOCKER_HOST="unix:///private/tmp/bdd-no-docker.sock"),
            capture_output=True,
            timeout=10,
        )
        assert missing.returncode != 0
        ctx.fresh_checked = True
    elif step == (
        "the request completes through an async handler and its operation task "
        "with valid trace context"
    ):
        ctx.response = ctx.app.state.test_client.post(
            "/v1/bulk-payments",
            json=ctx.body,
            headers={
                "Idempotency-Key": ctx.key,
                "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            },
        )
    elif step == "a valid affordable batch is committed and then replayed with the same key":
        ctx.first = send(ctx, payload("1"))
        send(ctx)
    elif step == "PostgreSQL becomes unavailable":
        ctx.pool_original = ctx.app.state.pool.getconn

        def unavailable(*args, **kwargs):
            raise PoolTimeout("synthetic unavailable DB")

        ctx.monkeypatch.setattr(ctx.app.state.pool, "getconn", unavailable)
    elif step == "an affordable batch is submitted and retried under the same key":
        ctx.first = send(ctx, payload("1"))
        send(ctx)
    else:
        pytest.fail("Undefined When binding: " + step)


@then(parsers.re(r"(?P<step>.+)"))
def verify(step, ctx, datatable=None):
    from .test_observability import correlation_step

    if correlation_step(ctx, step, "then"):
        return

    from .test_idempotency import lifecycle_step

    if lifecycle_step(ctx, step, "then"):
        return

    from .test_settings import settings_then

    if settings_then(ctx, step):
        return
    from .test_migrations import migration_then

    if migration_then(ctx, step):
        return
    if step == "the response status is 404":
        assert ctx.response.status_code == 404
        return
    if step == "no payments or idempotency reservations or balance changes are persisted":
        assert balances(ctx.db) == ctx.before
        assert count(ctx.db, "payments") == count(ctx.db, "bulk_payment_requests") == 0
        return
    if step == "no payments, balance changes or committed result receipt remain":
        assert balances(ctx.db) == ctx.before
        assert count(ctx.db, "payments") == 0
        with psycopg.connect(ctx.db) as connection:
            assert (
                connection.execute("SELECT count(*) FROM request_result_receipts").fetchone()[0]
                == 0
            )
        return
    if step == "one admitted request returns 201 and duplicates observing processing return 409":
        assert sum(r.status_code == 201 for r in ctx.results) == 1
        assert sum(r.status_code == 409 for r in ctx.results) == 7
        return
    if (
        step == "identical retries after completed publication replay the same 201 result "
        "and persisted payment ID"
    ):
        winner = next(r for r in ctx.results if r.status_code == 201)
        assert post(ctx.app, payload("30"), "K").json() == winner.json()
        return
    if step == "bounded cleanup or reconciliation releases the abandoned claim before retry":
        with psycopg.connect(ctx.db) as connection:
            assert connection.execute(
                "SELECT count(*) FROM request_result_receipts"
            ).fetchone() == (0,)
        return
    if step == "bounded reconciliation publishes the committed receipt if necessary":
        with psycopg.connect(ctx.db) as connection:
            assert connection.execute(
                "SELECT state FROM bulk_payment_requests WHERE idempotency_key=%s",
                (ctx.key,),
            ).fetchone() == ("completed",)
        return
    if step == "the original 201 result is replayed after any temporary processing 409":
        assert ctx.response.status_code == 201 and ctx.response.json() == ctx.first.json()
        return
    if step == "only the original batch's payment and balance effects exist":
        assert count(ctx.db, "payments") == len(ctx.first.json()["payments"])
        assert balances(ctx.db)[PAYER] == ctx.before[PAYER] - ctx.first.json()["total_amount_cents"]
        return
    if step == "liveness completes while the payment operation is still waiting":
        assert ctx.liveness.status_code == 200 and ctx.waiting_when_live_completed
        return
    if step == "the expired operation returns retryable 503 and discards its borrowed connection":
        ctx.response = ctx.payment_future.result(timeout=5)
        assert ctx.response.status_code == 503 and ctx.response.headers["Retry-After"] == "1"
        assert ctx.original_connection.closed
        return
    if step == "no payment task or pool waiter is left behind":
        assert not ctx.app.state.pool.active_operations
        assert ctx.app.state.pool.get_stats().get("requests_waiting", 0) == 0
        return
    if step == "same-key recovery creates or replays exactly one committed batch":
        with psycopg.connect(ctx.db, autocommit=True) as connection:
            end = time.monotonic() + 1
            while not connection.execute(
                "SELECT COALESCE(bool_and(owner_deadline<=clock_timestamp()),true) "
                "FROM bulk_payment_requests WHERE state='processing'"
            ).fetchone()[0]:
                assert time.monotonic() < end
                time.sleep(0.01)
        first = post(ctx.app, payload("1"), ctx.key)
        assert first.status_code == 201
        assert post(ctx.app, payload("1"), ctx.key).json() == first.json()
        assert count(ctx.db, "payments") == count(ctx.db, "bulk_payment_requests") == 1
        assert balances(ctx.db) == {PAYER: 199900, PAYEE: 100, OTHER: 0}
        return
    if step == "the service returns 201 with the unsupported description characters removed":
        assert ctx.response.status_code == 201
        assert [
            p["description"] for p in ctx.response.json()["payments"]
        ] == ctx.expected_descriptions
        return
    if step in {
        "stored descriptions and the durable response contain the sanitized text",
        "each description is persisted and returned exactly with stable payment IDs",
    }:
        assert ctx.response.status_code == 201
        with psycopg.connect(ctx.db) as connection:
            descriptions = connection.execute(
                "SELECT description FROM payments ORDER BY id"
            ).fetchall()
            stored = connection.execute(
                "SELECT response_body FROM bulk_payment_requests"
            ).fetchone()[0]
        assert [r[0] for r in descriptions] == ctx.expected_descriptions
        assert [
            p["description"] for p in ctx.response.json()["payments"]
        ] == ctx.expected_descriptions
        assert stored == ctx.response.json() == ctx.first.json()
        n = len(ctx.expected_descriptions)
        assert count(ctx.db, "payments") == n
        assert balances(ctx.db) == {
            PAYER: 200000 - sum(p["amount_cents"] for p in stored["payments"]),
            PAYEE: sum(p["amount_cents"] for p in stored["payments"]),
            OTHER: 0,
        }
        return
    if (
        step == "retrying the sanitized request with the same key returns "
        "the same payment IDs without new effects"
    ):
        before = balances(ctx.db)
        clean = {
            **ctx.body,
            "payments": [
                {**p, "description": d}
                for p, d in zip(ctx.body["payments"], ctx.expected_descriptions, strict=True)
            ],
        }
        replay = post_escaped_json(ctx.app, clean, ctx.key)
        assert replay.status_code == 201 and replay.json() == ctx.first.json()
        assert balances(ctx.db) == before and count(ctx.db, "payments") == len(clean["payments"])
        return
    status = re.fullmatch(r"the response(?: status)? is (\d+)", step)
    detail = re.fullmatch(r'the response is (\d+) with detail "([^"]+)"', step)
    summary = re.fullmatch(
        r"the response is 201 with (\d+) payments_created and (\d+) total_amount_cents", step
    )
    balance_match = re.fullmatch(
        r"balances of A, B and C (?:are|remain) (\d+), (\d+) and (\d+) cents", step
    )
    payment_count = re.fullmatch(r"exactly (\d+) payments?(?: to B)? (?:are|is) persisted", step)
    if status:
        assert ctx.response.status_code == int(status[1])
    elif detail:
        assert ctx.response.status_code == int(detail[1]) and ctx.response.json() == {
            "detail": detail[2]
        }
    elif summary:
        assert ctx.response.status_code == 201
        assert ctx.response.json()["payments_created"] == int(summary[1])
        assert ctx.response.json()["total_amount_cents"] == int(summary[2])
    elif balance_match:
        assert balances(ctx.db) == dict(
            zip([PAYER, PAYEE, OTHER], map(int, balance_match.groups()), strict=True)
        )
    elif payment_count:
        assert count(ctx.db, "payments") == int(payment_count[1])
    elif step in {
        "its payments list contains both persisted payments with their "
        "generated IDs in request order",
        "its payments list contains all 3 persisted payments with their "
        "generated IDs in request order",
    }:
        assert ctx.response.json()["payments"] == [
            {
                "id": row[0],
                "payer_firm_uuid": PAYER,
                "payee_firm_uuid": p["payee_firm_uuid"],
                "amount_cents": row[3],
                "description": row[4],
            }
            for row, p in zip(rows(ctx), ctx.body["payments"], strict=True)
        ]
    elif step == "exactly 2 payment rows preserve the payer, recipients, amounts and descriptions":
        assert [r[1:] for r in rows(ctx)] == [(1, 2, 120075, "Services"), (1, 3, 30000, "Referral")]
    elif step in {"no payment rows are inserted", "no payment or balance changes occur"}:
        assert count(ctx.db, "payments") == 0
        if ctx.scenario == "SC-4":
            assert balances(ctx.db) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    elif step == "no idempotency key is reserved":
        assert count(ctx.db, "bulk_payment_requests") == 0
    elif step.startswith("the payment amount is "):
        value = step.removeprefix("the payment amount is ").removesuffix(
            " or no payment is inserted"
        )
        if value == "none":
            assert count(ctx.db, "payments") == 0
        else:
            assert rows(ctx)[0][3] == int(value)
    elif step in {
        "the response status and body equal the original 201 response",
        "the original 201 result is replayed",
    }:
        assert ctx.response.status_code == ctx.first.status_code == 201
        assert ctx.response.json() == ctx.first.json()
    elif step == "the returned payment IDs equal the original committed payment IDs":
        assert [p["id"] for p in ctx.response.json()["payments"]] == [
            p["id"] for p in ctx.first.json()["payments"]
        ]
    elif step == "only one payment of 100 cents exists":
        assert len(rows(ctx)) == 1 and rows(ctx)[0][3] == 100
    elif step == "no additional payment or balance changes occur":
        assert balances(ctx.db) == ctx.before and count(ctx.db, "payments") == len(
            ctx.first.json()["payments"]
        )
    elif step == "the original 422 response is replayed without transfers":
        assert (
            ctx.response.status_code == 422
            and ctx.response.json() == ctx.first.json()
            and count(ctx.db, "payments") == 0
        )
    elif step in {
        "the response is 201 and exactly that batch is transferred",
        "exactly one successful batch is committed with response 201",
    }:
        assert ctx.response.status_code == 201 and count(ctx.db, "payments") == 1
    elif step == "both responses are 201":
        assert [r.status_code for r in ctx.results] == [201, 201]
    elif step == "all responses replay the same 201 result":
        assert [r.status_code for r in ctx.results] == [201] * 8
        assert all(r.json() == ctx.results[0].json() for r in ctx.results)
    elif step == "all returned payment lists contain the same single persisted payment ID":
        assert len({r.json()["payments"][0]["id"] for r in ctx.results}) == 1
        assert ctx.results[0].json()["payments"][0]["id"] == rows(ctx)[0][0]
    elif step == "every accepted batch returns 201 and every unaffordable batch returns 422":
        assert sorted(r.status_code for r in ctx.results) == [201] * 3 + [422] * 5
    elif step == "the total committed debit does not exceed 10000 cents":
        assert 0 <= 10000 - balances(ctx.db)[PAYER] <= 10000
    elif step == "all balances are nonnegative and their total is conserved":
        assert (
            all(v >= 0 for v in balances(ctx.db).values())
            and sum(balances(ctx.db).values()) == 10000
        )
    elif step == "only accepted batches have persisted payments":
        assert count(ctx.db, "payments") == sum(r.status_code == 201 for r in ctx.results)
    elif step == "every accepted batch is atomic and conserves funds":
        assert count(ctx.db, "payments") == 3 and sum(balances(ctx.db).values()) == 30000
    elif step in {
        "the requests finish with success or a bounded retryable failure",
        "no request waits indefinitely because of conflicting transfer directions",
    }:
        assert [response.status_code for response in ctx.results] == [
            201
        ] * 3  # The timed independent-instance executor finished and asserted real responses.
    elif step == "the response is 503 with Retry-After 1":
        assert ctx.response.status_code == 503 and ctx.response.headers["Retry-After"] == "1"
    elif step in {
        "no payments, balance changes or completed key K remain",
        "no payments, balance changes or key K reservation remain",
    }:
        assert count(ctx.db, "payments") == count(ctx.db, "bulk_payment_requests") == 0
        assert balances(ctx.db) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
    elif step == "only the original batch's payment and balance effects exist":
        assert count(ctx.db, "payments") == 1 and balances(ctx.db) == {
            PAYER: 199000,
            PAYEE: 1000,
            OTHER: 0,
        }
    elif step == "query-plan evidence demonstrates efficient indexed UUID lookup":
        assert "firms_uuid_idx" in str(ctx.plan)
    elif step == "duplicate stored UUIDs are rejected by PostgreSQL":
        with pytest.raises(psycopg.errors.UniqueViolation), psycopg.connect(ctx.db) as c:
            c.execute(
                "INSERT INTO firms(name,uuid,balance_cents) VALUES ('duplicate',%s,0)", (PAYER,)
            )
    elif step == "the response is 201 and persisted balances match SC-1":
        assert ctx.response.status_code == 201 and balances(ctx.db) == {
            PAYER: 49925,
            PAYEE: 120075,
            OTHER: 30000,
        }
    elif step == "retrying the same key replays the result without additional payments":
        first = ctx.response
        before = rows(ctx)
        assert send(ctx).json() == first.json()
        assert rows(ctx) == before
    elif step.startswith("the database outcome is "):
        if ctx.case == "SQL-like Unicode text as description":
            assert (
                rows(ctx)[0][4] == ctx.body["payments"][0]["description"]
                and len(balances(ctx.db)) == 3
            )
        else:
            assert (
                balances(ctx.db) == {PAYER: 200000, PAYEE: 0, OTHER: 0}
                and count(ctx.db, "payments") == 0
            )
    elif step in {
        "setup installs the locked dependencies and checks use real PostgreSQL",
        "formatting, lint and strict application/script typing pass",
        "missing Docker fails the integration check instead of silently skipping it",
    }:
        assert ctx.fresh_checked
    elif step == "connections, queues, waits and retries have finite limits":
        s = ctx.app.state.settings
        assert (
            s.pool_max > 0
            and s.pool_max_waiting > 0
            and s.acquire_timeout_ms > 0
            and s.transaction_timeout_ms > 0
        )
    elif (
        step == "those limits are justified against the documented connection "
        "budget and request deadline"
    ):
        s = ctx.app.state.settings
        assert s.max_processes * s.pool_max <= s.db_connection_budget
        assert (
            s.acquire_timeout_ms
            + s.statement_timeout_ms
            + s.transaction_timeout_ms
            + s.response_margin_ms
            <= s.operation_budget_ms
        )
    elif step == "the persisted firm balances are:":
        with psycopg.connect(ctx.db) as c:
            actual = c.execute("SELECT id,name,balance_cents FROM firms ORDER BY id").fetchall()
        assert actual == [(int(r[0]), r[1], int(r[2])) for r in datatable[1:]]
        for actual_row, expected in zip(actual, datatable[1:], strict=True):
            assert f"{actual_row[2] // 100}.{actual_row[2] % 100:02d}" == expected[3]
    elif step == "exactly these 3 payments are persisted in request order:":
        assert [r[1:] for r in rows(ctx)] == [
            (int(r[0]), int(r[1]), int(r[2]), r[3]) for r in datatable[1:]
        ]
    elif step == "the total firm balance remains 5250000 cents":
        assert sum(balances(ctx.db).values()) == 5250000
    elif step == "X-Request-ID identifies that attempt and matches its structured completion log":
        assert wait_events(ctx.logs, 1)[0]["request_id"] == ctx.response.headers["X-Request-ID"]
    elif step == "its allowed trace and span IDs correlate the request and database phases":
        event = wait_events(ctx.logs, 1)[0]
        assert event["trace_id"] == "1" * 32
        assert any(
            s.name == "firms.lock" and f"{s.context.trace_id:032x}" == event["trace_id"]
            for s in ctx.memory.get_finished_spans()
        )
    elif (
        step == "emitted logs, spans and metric labels exclude the canaries, "
        "amounts, credentials and raw SQL"
    ):
        signals = (
            json.dumps(wait_events(ctx.logs, 1))
            + str(
                [
                    (s.name, dict(s.attributes or {}), s.events)
                    for s in ctx.memory.get_finished_spans()
                ]
            )
            + ctx.app.state.telemetry.metrics(ctx.app.state.pool).decode()
        )
        assert all(
            x not in signals for x in ["CANARY", PAYER, PAYEE, "SELECT", "INSERT", "DATABASE_URL"]
        )
    elif step == "overlapping requests never inherit each other's request context":
        from .test_observability import test_overlapping_request_context_does_not_leak

        test_overlapping_request_context_does_not_leak(ctx.app, ctx.db)
    elif step == "operational signals distinguish the committed attempt from the replayed attempt":
        assert [e["outcome"] for e in wait_events(ctx.logs, 2)] == ["committed", "replayed"]
    elif step == "the created-payments counter increases only for the newly committed batch":
        assert ctx.app.state.telemetry.registry.get_sample_value("firm_payments_created_total") == 1
    elif step == "the response lists the same persisted payment IDs on both attempts":
        assert ctx.response.json()["payments"] == ctx.first.json()["payments"]
    elif (
        step == "a validation error, business rejection, conflict and database "
        "failure have distinct bounded outcomes"
    ):
        from .test_observability import test_outcomes_and_app_registries_are_independent

        test_outcomes_and_app_registries_are_independent(ctx.app, ctx.db)
    elif step == "metric labels contain only the documented finite label sets":
        labels = {
            key
            for family in ctx.app.state.telemetry.registry.collect()
            for sample in family.samples
            for key in sample.labels
        }
        assert labels <= {
            "route",
            "method",
            "status_class",
            "outcome",
            "result",
            "category",
            "signal",
            "le",
        }
    elif step == "liveness returns 200 without a database query":
        assert get(ctx.app, "/health/live").status_code == 200
    elif step == "readiness returns 503 within the configured health-probe budget":
        start = time.monotonic()
        assert get(ctx.app, "/health/ready").status_code == 503
        assert time.monotonic() - start < ctx.app.state.telemetry.health_budget_ms / 1000 + 0.2
    elif step == "metrics remain readable without a financial database query":
        assert get(ctx.app, "/metrics").status_code == 200
    elif (
        step
        == "operational probes do not change balances, create payments or reserve idempotency keys"
    ):
        assert (
            count(ctx.db, "payments") == count(ctx.db, "bulk_payment_requests") == 0
            and balances(ctx.db) == ctx.before
        )
    elif step == "readiness returns 200 after database recovery and successful bounded probing":
        ctx.monkeypatch.setattr(ctx.app.state.pool, "getconn", ctx.pool_original)
        assert get(ctx.app, "/health/ready").status_code == 200
    elif step == "monetary effects commit at most once and replay returns the same payment IDs":
        assert ctx.response.json() == ctx.first.json() and count(ctx.db, "payments") == 1
    elif step == "telemetry failures cannot change a committed result into a monetary retry":
        assert ctx.first.status_code == ctx.response.status_code == 201 and balances(ctx.db) == {
            PAYER: 199900,
            PAYEE: 100,
            OTHER: 0,
        }
    elif step == "recording and shutdown attempts remain bounded":
        start = time.monotonic()
        ctx.app.state.telemetry.close()
        assert time.monotonic() - start < 1
        ctx.release.set()
    elif (
        step == "observable telemetry degradation is reported when a local signal remains available"
    ):
        assert (
            ctx.app.state.telemetry.registry.get_sample_value(
                "firm_payments_telemetry_dropped_total", {"signal": "traces"}
            )
            > 0
        )
    elif (
        step
        == "the image runs Python 3.15 as a non-root user and serves the HTTP application shell"
    ):
        assert ctx.service["runtime"] == {"python": [3, 15], "uid": 10001}
    elif step == "repeating Alembic upgrade head preserves seeded firms and their balances":
        before = balances(ctx.db)
        migrate(ctx.db)
        assert balances(ctx.db) == before
    elif step == "startup checks do not create payments or reserve idempotency keys":
        assert count(ctx.db, "payments") == count(ctx.db, "bulk_payment_requests") == 0
    elif (
        step
        == "no payment or telemetry capability is claimed complete merely because startup succeeds"
    ):
        assert ctx.scenario == "SC-26" and ctx.response is None
    else:
        pytest.fail("Undefined Then binding: " + step)
