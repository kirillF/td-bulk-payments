"""Explicit Alembic upgrade entry point; never called from payment requests."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util import CommandError

from app.core.config import load_environment

ROOT = Path(__file__).resolve().parents[2]


def migration_config(database_url: str | None = None) -> Config:
    configuration = Config(str(ROOT / "alembic.ini"))
    if database_url is not None:
        configuration.attributes["database_url"] = database_url
    return configuration


def migrate(database_url: str) -> None:
    command.upgrade(migration_config(database_url), "head")


if __name__ == "__main__":
    try:
        migrate(load_environment()["DATABASE_URL"])
    except CommandError, KeyError:
        raise SystemExit(
            "Database migration failed; verify configuration and schema compatibility"
        ) from None
