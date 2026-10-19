"""NFR-3 configuration algebra, independent of incidental test scheduling."""

import pytest

from app.core.config import BUDGET_FIELDS, Settings


def test_configured_requires_explicit_budgets():
    with pytest.raises(ValueError, match="Explicit deployment budget"):
        Settings.from_env({"DATABASE_URL": "postgresql://fixture"})
    local = Settings.local("postgresql://fixture")
    env = {"DATABASE_URL": local.database_url, "PAYMENTS_PROFILE": "configured"}
    for name in BUDGET_FIELDS:
        env["PAYMENTS_" + name.upper()] = str(getattr(local, name))
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


def test_sources_constructor_priority_and_isolation(tmp_path, monkeypatch):
    from app.core import config

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql://file\nPAYMENTS_PROFILE=local\n"
        "PAYMENTS_LOG_QUEUE_CAPACITY=11\nPAYMENTS_TRACE_QUEUE_CAPACITY=13\n"
    )
    monkeypatch.setattr(config, "ENV_FILE", dotenv)
    monkeypatch.setenv("DATABASE_URL", "postgresql://process")
    monkeypatch.setenv("PAYMENTS_PROFILE", "local")
    monkeypatch.setenv("PAYMENTS_LOG_QUEUE_CAPACITY", "12")
    explicit = Settings(database_url="postgresql://explicit", profile="local", pool_max_waiting=9)
    assert explicit.database_url == "postgresql://explicit"
    assert explicit.log_queue_capacity == 12 and explicit.trace_queue_capacity == 13
    assert explicit.pool_max_waiting == 9
    isolated = Settings.local("postgresql://isolated")
    assert isolated.log_queue_capacity == 256 and isolated.trace_queue_capacity == 512
    assert (
        Settings.from_env(
            {"PAYMENTS_PROFILE": "local", "DATABASE_URL": "postgresql://map"}
        ).database_url
        == "postgresql://map"
    )
    with pytest.raises(ValueError):
        Settings.from_env({"PAYMENTS_PROFILE": "local"})


@pytest.mark.parametrize(
    "override",
    [
        {"pool_max": True},
        {"pool_max": 4.0},
        {"log_level": "VERBOSE"},
        {"log_queue_capacity": 0},
        {"trace_sample_ratio": float("nan")},
        {"trace_sample_ratio": 1.1},
    ],
)
def test_new_settings_reject_invalid_values_without_secret_diagnostics(override):
    secret = "postgresql://user:private-canary@invalid/database"
    with pytest.raises(ValueError) as captured:
        Settings.local(secret, **override)
    assert secret not in str(captured.value) and "private-canary" not in repr(captured.value)


def test_log_level_normalization_and_actual_filtering():
    import json
    import threading

    from app.core.observability import Telemetry

    captured, complete = [], threading.Event()

    def sink(line):
        value = json.loads(line)
        captured.append(value)
        if value["event"] == "test.warning":
            complete.set()

    settings = Settings.local("postgresql://fixture", log_level="warning")
    assert settings.log_level == "WARNING"
    telemetry = Telemetry(settings=settings, sink=sink)
    try:
        telemetry.event("test.debug", level="DEBUG")
        telemetry.event("test.info", level="INFO")
        telemetry.event("test.warning", level="WARNING")
        assert complete.wait(1)
        assert [value["event"] for value in captured] == ["test.warning"]
    finally:
        telemetry.close()


def settings_given(ctx, step):
    if (
        step
        == "conflicting values in explicit configuration, process environment and literal dotenv"
    ):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from app.core import config

        directory = Path(ctx.stack.enter_context(TemporaryDirectory()))
        dotenv = directory / ".env"
        dotenv.write_text(
            "DATABASE_URL=postgresql://file\nPAYMENTS_PROFILE=local\n"
            "PAYMENTS_LOG_QUEUE_CAPACITY=11\nPAYMENTS_TRACE_QUEUE_CAPACITY=13\n"
        )
        ctx.monkeypatch.setattr(config, "ENV_FILE", dotenv)
        ctx.monkeypatch.setenv("DATABASE_URL", "postgresql://process")
        ctx.monkeypatch.setenv("PAYMENTS_PROFILE", "local")
        ctx.monkeypatch.setenv("PAYMENTS_LOG_QUEUE_CAPACITY", "12")
        return True
    if step.startswith("configuration contains "):
        problem = step.removeprefix("configuration contains ")
        base = {"DATABASE_URL": "postgresql://private-canary", "PAYMENTS_PROFILE": "local"}
        variants = {
            "unsupported log level": [{**base, "PAYMENTS_LOG_LEVEL": "VERBOSE"}],
            "missing configured deployment budget": [{**base, "PAYMENTS_PROFILE": "configured"}],
            "inconsistent database timeout bounds": [{**base, "PAYMENTS_LOCK_TIMEOUT_MS": "3000"}],
            "invalid queue bound or sample ratio": [
                {**base, "PAYMENTS_LOG_QUEUE_CAPACITY": "0"},
                {**base, "PAYMENTS_TRACE_SAMPLE_RATIO": "nan"},
            ],
        }
        ctx.invalid_settings = variants[problem]
        return True
    return False


def settings_when(ctx, step):
    if step == "normal settings are loaded":
        ctx.settings = Settings(database_url="postgresql://explicit", profile="local")
        return True
    if step == "settings are constructed from an explicit test mapping":
        ctx.monkeypatch.setenv("PAYMENTS_POOL_MAX", "invalid-ambient")
        ctx.settings = Settings.from_env(
            {"DATABASE_URL": "postgresql://map", "PAYMENTS_PROFILE": "local"}
        )
        return True
    if step == "the service starts" and hasattr(ctx, "invalid_settings"):
        from fastapi.testclient import TestClient

        from app.main import create_app

        original = Settings.from_env
        ctx.configuration_failures = []
        for mapping in ctx.invalid_settings:
            with ctx.monkeypatch.context() as patch:
                patch.setattr(
                    Settings, "from_env", classmethod(lambda cls, values=mapping: original(values))
                )
                with pytest.raises(ValueError) as failure:
                    with TestClient(create_app()):
                        pytest.fail("Invalid settings must not serve requests")
                ctx.configuration_failures.append(str(failure.value))
        return True
    return False


def settings_then(ctx, step):
    if (
        step == "explicit configuration precedes environment, which precedes "
        "dotenv and permitted defaults"
    ):
        assert ctx.settings.database_url == "postgresql://explicit"
        assert ctx.settings.log_queue_capacity == 12 and ctx.settings.trace_queue_capacity == 13
        assert ctx.settings.pool_max == 4
        return True
    if step == "configured-profile budgets remain required and secrets are never logged":
        with pytest.raises(ValueError) as failure:
            Settings.from_env({"DATABASE_URL": "postgresql://private-canary"})
        assert "private-canary" not in str(failure.value)
        assert not any("private-canary" in line for line in ctx.logs)
        return True
    if (
        step
        == "ambient environment and dotenv cannot supply missing values or override the mapping"
    ):
        assert ctx.settings.database_url == "postgresql://map"
        assert ctx.settings.log_queue_capacity == 256 and ctx.settings.trace_queue_capacity == 512
        with pytest.raises(ValueError):
            Settings.from_env({"PAYMENTS_PROFILE": "local"})
        return True
    if step == "startup fails with a safe diagnostic and no financial changes":
        from .conftest import balances, count

        assert ctx.configuration_failures
        assert all("private-canary" not in message for message in ctx.configuration_failures)
        assert balances(ctx.db) == ctx.before and count(ctx.db, "payments") == 0
        return True
    return False


def test_single_connection_pool_needs_no_partition_setting():
    settings = Settings.local("postgresql://isolated", pool_max=1)
    assert settings.pool_max == 1
    assert "idempotency_pool_max" not in Settings.model_fields
