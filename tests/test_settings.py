"""NFR-3 configuration algebra, independent of incidental test scheduling."""

from dataclasses import fields

import pytest

from app.core.config import Settings


def test_configured_requires_explicit_budgets():
    with pytest.raises(ValueError, match="Explicit deployment budget"):
        Settings.from_env({"DATABASE_URL": "postgresql://fixture"})
    local = Settings.local("postgresql://fixture")
    env = {"DATABASE_URL": local.database_url, "PAYMENTS_PROFILE": "configured"}
    for field in fields(local):
        if field.name not in {"database_url", "profile"}:
            env["PAYMENTS_" + field.name.upper()] = str(getattr(local, field.name))
    configured = Settings.from_env(env)
    assert configured.profile == "configured"
    assert configured.max_processes * configured.pool_max <= configured.db_connection_budget


@pytest.mark.parametrize(
    "override",
    [
        {"pool_max_waiting": 0},
        {"pool_max": 0},
        {"pool_min": 5},
        {"max_processes": 3},
        {"lock_timeout_ms": 3000},
        {"statement_timeout_ms": 10000},
        {"idle_transaction_timeout_ms": 10000},
        {"operation_budget_ms": 14999},
        {"acquire_timeout_ms": -1},
        {"connect_timeout_seconds": 0},
    ],
)
def test_invalid_budgets_fail(override):
    with pytest.raises(ValueError):
        Settings.local("postgresql://fixture", **override)


def test_local_profile_and_invalid_env():
    settings = Settings.from_env(
        {"DATABASE_URL": "postgresql://fixture", "PAYMENTS_PROFILE": "local"}
    )
    assert (settings.pool_max, settings.pool_max_waiting) == (4, 4)
    with pytest.raises(ValueError):
        Settings.from_env(
            {
                "DATABASE_URL": "postgresql://fixture",
                "PAYMENTS_PROFILE": "local",
                "PAYMENTS_POOL_MAX": "1.5",
            }
        )


def test_dotenv_defaults_environment_precedence_and_mapping_isolation(tmp_path, monkeypatch):
    import os

    from app.core import config

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql://file\nPAYMENTS_PROFILE=local\n"
        "PAYMENTS_POOL_MAX_WAITING=7\nLITERAL=${NO_INTERPOLATION}\n"
    )
    monkeypatch.setattr(config, "ENV_FILE", dotenv)
    for key in list(os.environ):
        if key.startswith("PAYMENTS_") or key == "DATABASE_URL":
            monkeypatch.delenv(key)
    before = dict(os.environ)
    assert config.Settings.from_env().database_url == "postgresql://file"
    assert config.Settings.from_env().pool_max_waiting == 7
    assert config.load_environment()["LITERAL"] == "${NO_INTERPOLATION}"
    assert dict(os.environ) == before
    monkeypatch.setenv("DATABASE_URL", "postgresql://process")
    assert config.Settings.from_env().database_url == "postgresql://process"
    isolated = config.Settings.from_env(
        {"DATABASE_URL": "postgresql://explicit", "PAYMENTS_PROFILE": "local"}
    )
    assert isolated.database_url == "postgresql://explicit" and isolated.pool_max_waiting == 4
    dotenv.unlink()
    monkeypatch.setenv("PAYMENTS_PROFILE", "local")
    assert config.Settings.from_env().database_url == "postgresql://process"
    monkeypatch.setenv("PAYMENTS_POOL_MAX_WAITING", "invalid")
    with pytest.raises(ValueError, match="integer"):
        config.Settings.from_env()


def test_dotenv_files_are_excluded_and_template_is_trackable():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", ".env", ".env.secret", ".env.example"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [".env", ".env.secret"]
    exclusions = (root / ".dockerignore").read_text().splitlines()
    assert ".env" in exclusions and ".env.*" in exclusions
