"""Explicit deployment budgets; local defaults are illustrative, never capacity claims."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Self

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Load literal local defaults without modifying the process environment."""
    if env is not None:
        return dict(env)
    defaults = {
        key: value
        for key, value in dotenv_values(ENV_FILE, interpolate=False).items()
        if value is not None
    }
    return defaults | dict(os.environ)


@dataclass(frozen=True)
class Settings:
    database_url: str
    profile: str = "local"
    db_connection_budget: int = 8
    max_processes: int = 2
    pool_min: int = 1
    pool_max: int = 4
    pool_max_waiting: int = 4
    acquire_timeout_ms: int = 1000
    connect_timeout_seconds: int = 2
    statement_timeout_ms: int = 3000
    lock_timeout_ms: int = 2000
    idle_transaction_timeout_ms: int = 5000
    transaction_timeout_ms: int = 10000
    operation_budget_ms: int = 15000
    response_margin_ms: int = 1000
    startup_timeout_ms: int = 10000

    def __post_init__(self) -> None:
        if not self.database_url or self.profile not in {"local", "configured"}:
            raise ValueError("A database URL and valid profile are required")
        for field in fields(self):
            if field.name in {"database_url", "profile"}:
                continue
            value = getattr(self, field.name)
            if type(value) is not int or value < (0 if field.name == "pool_min" else 1):
                raise ValueError(f"{field.name} must be a bounded positive integer")
        if (
            self.pool_min > self.pool_max
            or self.max_processes * self.pool_max > self.db_connection_budget
        ):
            raise ValueError("Pool/process counts exceed the declared connection budget")
        if not self.lock_timeout_ms < self.statement_timeout_ms < self.transaction_timeout_ms:
            raise ValueError("Require lock < statement < transaction timeouts")
        if self.idle_transaction_timeout_ms >= self.transaction_timeout_ms:
            raise ValueError("Idle transaction timeout must be below transaction timeout")
        if (
            self.acquire_timeout_ms
            + self.statement_timeout_ms
            + self.transaction_timeout_ms
            + self.response_margin_ms
            > self.operation_budget_ms
        ):
            raise ValueError("Database phases exceed the declared operation budget")

    @classmethod
    def local(cls, database_url: str, **overrides: int) -> Self:
        base = cls(database_url)
        values = {
            f.name: getattr(base, f.name)
            for f in fields(base)
            if f.name not in {"database_url", "profile"}
        }
        values.update(overrides)
        return cls(database_url, **values)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        env = load_environment(env)
        profile = env.get("PAYMENTS_PROFILE", "configured")
        values: dict[str, int] = {}
        for field in fields(cls):
            if field.name in {"database_url", "profile"}:
                continue
            key = "PAYMENTS_" + field.name.upper()
            if key not in env:
                if profile == "configured":
                    raise ValueError(f"Explicit deployment budget required: {key}")
                continue
            try:
                values[field.name] = int(env[key])
            except ValueError:
                raise ValueError(f"{key} must be an integer") from None
        return cls(database_url=env.get("DATABASE_URL", ""), profile=profile, **values)
