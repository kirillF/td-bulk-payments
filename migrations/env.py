"""Migration-only Core connection; lock before Alembic inspects version state."""

from contextlib import nullcontext

import psycopg
from alembic import context
from alembic.util import CommandError
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.pool import NullPool

from app.core.config import load_environment
from app.db.database import validate_schema_sync


def run(connection: Connection) -> None:
    # The outer transaction owns both application DDL and Alembic version changes.
    with nullcontext() if connection.in_transaction() else connection.begin():
        connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(583190217)")
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
        driver = connection.connection.driver_connection
        if not isinstance(driver, psycopg.Connection):
            raise CommandError("Unsupported migration connection")
        validate_schema_sync(driver)


def online() -> None:
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        run(supplied)
        return
    raw_url = context.config.attributes.get("database_url") or load_environment()["DATABASE_URL"]
    url = make_url(raw_url)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise CommandError("Unsupported migration database driver")
    engine = create_engine(
        url.set(drivername="postgresql+psycopg"),
        poolclass=NullPool,
        connect_args={"connect_timeout": 5, "application_name": "firm-payments-migrate"},
        hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()


try:
    if context.is_offline_mode():
        raise CommandError("Offline migration cannot verify database compatibility")
    online()
except Exception:
    # Direct CLI and programmatic callers get no raw SQL/driver/URL diagnostics.
    raise CommandError(
        "Database migration failed; verify configuration and schema compatibility"
    ) from None
