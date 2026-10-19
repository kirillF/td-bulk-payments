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
        return {"relations": present, "versions": versions, "rows": rows, "sequences": sequences}


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
            "DROP FUNCTION IF EXISTS fail_initial_migration()"
        )


def migration_given(ctx, step):
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
        install_fault(ctx.migration_url)
        ctx.stack.callback(remove_fault, ctx.migration_url)
    ctx.migration_before = state(ctx.migration_url)
    return True


def migration_when(ctx, step):
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
