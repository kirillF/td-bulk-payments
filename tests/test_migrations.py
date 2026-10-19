"""Migration behavior on independently owned real PostgreSQL databases."""

import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from psycopg import sql

from app.db.migrate import migrate, migration_config

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


@contextmanager
def new_database(base_url):
    name = "migration_" + uuid.uuid4().hex
    parsed = urlsplit(base_url)
    url = urlunsplit(parsed._replace(path="/" + name))
    with psycopg.connect(base_url, autocommit=True, connect_timeout=3) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield url
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def head():
    return ScriptDirectory.from_config(migration_config()).get_current_head()


def state(url):
    with psycopg.connect(url, connect_timeout=3) as connection:
        present = connection.execute(
            "SELECT to_regclass('alembic_version'), to_regclass('firms'), "
            "to_regclass('payments'), to_regclass('bulk_payment_requests')"
        ).fetchone()
        versions = (
            connection.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num"
            ).fetchall()
            if present[0]
            else []
        )
        rows = {}
        sequences = {}
        for table, exists in zip(
            ["firms", "payments", "bulk_payment_requests"], present[1:], strict=True
        ):
            if exists:
                rows[table] = connection.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY 1,2").format(sql.Identifier(table))
                ).fetchall()
        for table in ["firms", "payments"]:
            if present[1 if table == "firms" else 2]:
                sequence = connection.execute(
                    "SELECT pg_get_serial_sequence(%s,'id')", (table,)
                ).fetchone()[0]
                if sequence:
                    # PostgreSQL supplies this qualified identifier, not request data.
                    sequences[table] = connection.execute(
                        sql.SQL("SELECT last_value,is_called FROM {}").format(
                            sql.Identifier(*sequence.split("."))
                        )
                    ).fetchone()
        structure = connection.execute(
            "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),"
            "a.attnotnull,a.attidentity,pg_get_expr(d.adbin,d.adrelid) "
            "FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid "
            "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
            "WHERE c.relnamespace=current_schema()::regnamespace "
            "AND c.relname IN ('firms','payments','bulk_payment_requests') "
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"
        ).fetchall()
        constraints = connection.execute(
            "SELECT c.relname,pg_get_constraintdef(x.oid),x.convalidated FROM pg_class c "
            "JOIN pg_constraint x ON x.conrelid=c.oid "
            "WHERE c.relnamespace=current_schema()::regnamespace "
            "AND c.relname IN ('firms','payments','bulk_payment_requests') ORDER BY 1,2"
        ).fetchall()
        indexes = connection.execute(
            "SELECT tablename,indexname,indexdef FROM pg_indexes "
            "WHERE schemaname=current_schema() "
            "AND tablename IN ('firms','payments','bulk_payment_requests') ORDER BY 1,2"
        ).fetchall()
        return {
            "relations": present,
            "versions": versions,
            "rows": rows,
            "sequences": sequences,
            "schema": (structure, constraints, indexes),
        }


def seed_history(url):
    from .conftest import post, running_app

    with psycopg.connect(url) as connection:
        connection.execute((ROOT / "examples/firms.sql").read_text())
    body = json.loads((ROOT / "examples/bulk-payment.json").read_text())
    with running_app(url) as app:
        response = post(app, body, "migration-retained")
        assert response.status_code == 201
    return body, response.json()


def empty_upgrade(url):
    migrate(url)
    value = state(url)
    assert value["versions"] == [(head(),)]
    assert len(value["rows"]) == 3 and all(not rows for rows in value["rows"].values())


def repeat_upgrade(url):
    migrate(url)
    seed_history(url)
    before = state(url)
    migrate(url)
    assert state(url) == before


def failed_upgrade(url):
    install_fault(url)
    try:
        with pytest.raises(CommandError):
            migrate(url)
        assert state(url)["relations"] == (None, None, None, None)
    finally:
        remove_fault(url)
    empty_upgrade(url)


def rejected_downgrade(url):
    migrate(url)
    seed_history(url)
    before = state(url)
    with pytest.raises(CommandError):
        command.downgrade(migration_config(url), "base")
    assert state(url) == before


@pytest.mark.parametrize(
    "check", [empty_upgrade, repeat_upgrade, failed_upgrade, rejected_downgrade]
)
def test_initial_revision_safety(database_url, check):
    with new_database(database_url) as url:
        check(url)


def install_fault(url):
    with psycopg.connect(url) as connection:
        connection.execute("""
            CREATE FUNCTION fail_initial_migration() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF to_regclass('firms') IS NOT NULL THEN
                RAISE EXCEPTION 'synthetic migration failure';
              END IF;
            END $$;
            CREATE EVENT TRIGGER migration_fault ON ddl_command_end
            WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION fail_initial_migration()
        """)


def remove_fault(url):
    with psycopg.connect(url) as connection:
        connection.execute(
            "DROP EVENT TRIGGER IF EXISTS migration_fault; "
            "DROP FUNCTION IF EXISTS fail_initial_migration(); "
            "DROP EVENT TRIGGER IF EXISTS recording_fault; "
            "DROP FUNCTION IF EXISTS install_version_fault(); "
            "DROP FUNCTION IF EXISTS reject_migration_head()"
        )


def migration_given(ctx, step):
    prefix = "a structurally compatible database whose recorded revision is "
    if step.startswith(prefix):
        ctx.migration_url = ctx.stack.enter_context(new_database(ctx.db))
        unsupported_revision(ctx.migration_url, step.removeprefix(prefix))
        ctx.migration_before = state(ctx.migration_url)
        return True

    if (
        step
        == "a complete compatible legacy schema with firms, payments and durable request results"
    ):
        ctx.migration_url = ctx.stack.enter_context(new_database(ctx.db))
        ctx.migration_body, ctx.migration_response = legacy_database(ctx.migration_url)
        ctx.migration_before = state(ctx.migration_url)
        return True
    if step == "no Alembic revision has been recorded":
        assert state(ctx.migration_url)["versions"] == []
        return True
    if step.startswith("a legacy application schema that is "):
        ctx.migration_url = ctx.stack.enter_context(new_database(ctx.db))
        legacy_database(ctx.migration_url)
        with psycopg.connect(ctx.migration_url) as connection:
            if step.endswith("partially present"):
                connection.execute("DROP TABLE bulk_payment_requests")
            else:
                connection.execute("ALTER TABLE firms DROP CONSTRAINT firms_balance_cents_check")
        ctx.migration_before = state(ctx.migration_url)
        return True
    if step == "an application database eligible for upgrade":
        ctx.migration_url = ctx.stack.enter_context(new_database(ctx.db))
        ctx.migration_before = state(ctx.migration_url)
        return True

    known = {
        "an empty application database without a recorded revision",
        "the supported head revision and seeded financial records",
        "an upgrade fails after changing schema but before completing revision recording",
    }
    if step not in known:
        return False
    ctx.migration_url = ctx.stack.enter_context(new_database(ctx.db))
    if step == "the supported head revision and seeded financial records":
        migrate(ctx.migration_url)
        seed_history(ctx.migration_url)
    if step == "an upgrade fails after changing schema but before completing revision recording":
        legacy_database(ctx.migration_url)
        install_recording_fault(ctx.migration_url)
        ctx.stack.callback(remove_fault, ctx.migration_url)
    ctx.migration_before = state(ctx.migration_url)
    return True


def migration_when(ctx, step):
    if step == "the service attempts to start":
        assert_startup_rejected(ctx.migration_url)
        ctx.migration_startup_rejected = True
        return True

    if step == "Alembic attempts to upgrade the database":
        with pytest.raises(CommandError):
            migrate(ctx.migration_url)
        return True
    if step == "independent migration processes overlap while upgrading that same database":
        ctx.migration_observed = concurrent_upgrades(ctx.migration_url)
        return True

    if step in {
        "Alembic upgrades the database to the service head revision",
        "Alembic upgrade head is run again",
    }:
        migrate(ctx.migration_url)
    elif step == "the failed upgrade transaction finishes":
        with pytest.raises(CommandError):
            migrate(ctx.migration_url)
    elif step == "the failure is removed and Alembic upgrade head is retried":
        remove_fault(ctx.migration_url)
        migrate(ctx.migration_url)
    elif step == "a downgrade that would remove the initial application schema is requested":
        with pytest.raises(CommandError):
            command.downgrade(migration_config(ctx.migration_url), "base")
    else:
        return False
    return True


def migration_then(ctx, step):
    if step == "initialization fails before payment requests can be served":
        assert ctx.migration_startup_rejected
        return True
    if step == "no financial or idempotency records are changed":
        assert state(ctx.migration_url) == ctx.migration_before
        return True

    if step == "the head revision is recorded only after compatibility verification":
        assert state(ctx.migration_url)["versions"] == [(head(),)]
        preserved(state(ctx.migration_url), ctx.migration_before)
        return True
    if step == "retained request results still replay without additional monetary effects":
        verify_replay(ctx.migration_url, ctx.migration_body, ctx.migration_response)
        return True
    if step in {
        "the upgrade fails without advancing the recorded revision",
        "all original application schema objects and data remain unchanged",
    }:
        assert state(ctx.migration_url) == ctx.migration_before
        return True
    if step == "successful callers observe one consistent head revision and schema":
        assert ctx.migration_observed == 2
        assert state(ctx.migration_url)["versions"] == [(head(),)]
        return True
    if step == "a caller exceeding its configured wait fails within its finite bound":
        assert ctx.migration_observed == 2  # Helper also observed and bounded actual timeout.
        return True
    if step == "no duplicate schema objects or financial changes are introduced":
        assert all(not rows for rows in state(ctx.migration_url)["rows"].values())
        return True

    if step in {
        "the supported application schema and head revision are committed together",
        "exactly the supported schema and head revision are committed",
        "the same single head revision remains recorded",
    }:
        actual = state(ctx.migration_url)
        assert actual["versions"] == [(head(),)]
        assert len(actual["rows"]) == 3
    elif step == "no financial or idempotency records are created by migration":
        assert all(not rows for rows in state(ctx.migration_url)["rows"].values())
    elif step == "all original records, balances and generated identities are preserved":
        actual = state(ctx.migration_url)
        assert actual["rows"] == ctx.migration_before["rows"]
        assert actual["sequences"] == ctx.migration_before["sequences"]
    elif step == "neither its partial schema changes nor its new revision are committed":
        assert state(ctx.migration_url) == ctx.migration_before
    elif step == "previously committed financial and request records are preserved":
        assert state(ctx.migration_url)["rows"] == ctx.migration_before["rows"]
    elif step == "the downgrade is rejected before changing application data or revision":
        assert state(ctx.migration_url) == ctx.migration_before
    else:
        return False
    return True


def legacy_database(url):
    migrate(url)
    body, response = seed_history(url)
    with psycopg.connect(url) as connection:
        connection.execute("DROP TABLE alembic_version")
    return body, response


def preserved(actual, before):
    for key in ["rows", "sequences", "schema"]:
        assert actual[key] == before[key]


def verify_replay(url, body, expected):
    from .conftest import post, running_app

    before = state(url)
    with running_app(url) as app:
        assert post(app, body, "migration-retained").json() == expected
    assert state(url) == before


def legacy_adoption(url):
    body, response = legacy_database(url)
    before = state(url)
    migrate(url)
    after = state(url)
    preserved(after, before)
    assert after["versions"] == [(head(),)]
    verify_replay(url, body, response)


def unsupported_legacy(url, condition):
    legacy_database(url)
    with psycopg.connect(url) as connection:
        if condition == "partially present":
            connection.execute("DROP TABLE bulk_payment_requests")
        else:
            connection.execute("ALTER TABLE firms DROP CONSTRAINT firms_balance_cents_check")
    before = state(url)
    with pytest.raises(CommandError):
        migrate(url)
    assert state(url) == before


def concurrent_upgrades(url):
    import os
    import subprocess
    import sys
    import time

    environment = dict(os.environ, DATABASE_URL=url)
    processes = []
    observed = 0
    try:
        with psycopg.connect(url) as locker, psycopg.connect(url, autocommit=True) as observer:
            locker.execute("SELECT pg_advisory_xact_lock(583190217)")
            for _ in range(2):
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "app.db.migrate"],
                        cwd=ROOT,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            deadline = time.monotonic() + 8
            while observed < 2:
                observed = observer.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND application_name='firm-payments-migrate' AND wait_event='advisory'"
                ).fetchone()[0]
                assert all(p.poll() is None for p in processes)
                assert time.monotonic() < deadline, (
                    "Must observe actual concurrent migration lock waits"
                )
                time.sleep(0.01)
            locker.commit()
        for process in processes:
            stdout, stderr = process.communicate(timeout=12)
            assert process.returncode == 0, (stdout, stderr)
        assert state(url)["versions"] == [(head(),)]
        before = state(url)
        # A separate contender exercises the actual finite lock timeout, not just success.
        with psycopg.connect(url) as locker, psycopg.connect(url, autocommit=True) as observer:
            locker.execute("SELECT pg_advisory_xact_lock(583190217)")
            start = time.monotonic()
            process = subprocess.Popen(
                [sys.executable, "-m", "app.db.migrate"],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            deadline = start + 8
            while not observer.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND application_name='firm-payments-migrate' AND wait_event='advisory'"
            ).fetchone()[0]:
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            process.communicate(timeout=13)
            assert process.returncode != 0 and time.monotonic() - start < 14
        assert state(url) == before
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
    return observed


def test_verified_legacy_adoption_and_replay(database_url):
    with new_database(database_url) as url:
        legacy_adoption(url)


@pytest.mark.parametrize("condition", ["partially present", "incompatible in structure"])
def test_unsupported_legacy_rejected_without_stamp(database_url, condition):
    with new_database(database_url) as url:
        unsupported_legacy(url, condition)


def test_independent_migration_processes_contend_and_expire(database_url):
    with new_database(database_url) as url:
        assert concurrent_upgrades(url) == 2


@pytest.mark.parametrize("entry", [["-m", "app.db.migrate"], ["-m", "alembic", "upgrade", "head"]])
def test_migration_cli_sanitizes_connection_failure(entry):
    import os
    import subprocess
    import sys

    secret = "SYNTHETIC_MIGRATION_SECRET"
    result = subprocess.run(
        [sys.executable, *entry],
        cwd=ROOT,
        env=dict(os.environ, DATABASE_URL=f"postgresql://fixture:{secret}@127.0.0.1:1/fixture"),
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def install_recording_fault(url):
    with psycopg.connect(url) as connection:
        connection.execute("""
            CREATE FUNCTION reject_migration_head() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic revision recording failure'; END $$;
            CREATE FUNCTION install_version_fault() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF to_regclass('alembic_version') IS NOT NULL THEN
                EXECUTE 'CREATE TRIGGER reject_migration_head BEFORE INSERT ON alembic_version
                         FOR EACH ROW EXECUTE FUNCTION reject_migration_head()';
              END IF;
            END $$;
            CREATE EVENT TRIGGER recording_fault ON ddl_command_end
            WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION install_version_fault()
        """)


def test_failed_revision_recording_preserves_legacy_financial_data(database_url):
    with new_database(database_url) as url:
        body, response = legacy_database(url)
        before = state(url)
        install_recording_fault(url)
        try:
            with pytest.raises(CommandError):
                migrate(url)
            assert state(url) == before
        finally:
            remove_fault(url)
        migrate(url)
        preserved(state(url), before)
        verify_replay(url, body, response)


def unsupported_revision(url, revision):
    migrate(url)
    seed_history(url)
    with psycopg.connect(url) as connection:
        if revision == "missing":
            connection.execute("DROP TABLE alembic_version")
        elif revision == "empty":
            connection.execute("DELETE FROM alembic_version")
        else:
            connection.execute(
                "UPDATE alembic_version SET version_num=%s",
                ("0000_older" if revision == "older" else "unknown_future",),
            )


def assert_startup_rejected(url):
    from .conftest import running_app

    with pytest.raises(ValueError, match="Unsupported database revision"):
        with running_app(url):
            pytest.fail("Unsupported migration revision must not serve requests")


@pytest.mark.parametrize("revision", ["missing", "empty", "older", "unknown"])
def test_startup_rejects_unsupported_revision_without_effects(database_url, revision):
    with new_database(database_url) as url:
        unsupported_revision(url, revision)
        before = state(url)
        assert_startup_rejected(url)
        assert state(url) == before
