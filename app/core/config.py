"""Typed runtime configuration; local values illustrate budgets, not capacity claims."""

import os
import re
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal, Self

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_ISOLATED: ContextVar[bool] = ContextVar("isolated_settings", default=False)
BUDGET_FIELDS = frozenset(
    {
        "db_connection_budget",
        "max_processes",
        "pool_min",
        "pool_max",
        "pool_max_waiting",
        "acquire_timeout_ms",
        "connect_timeout_seconds",
        "statement_timeout_ms",
        "lock_timeout_ms",
        "idle_transaction_timeout_ms",
        "transaction_timeout_ms",
        "operation_budget_ms",
        "response_margin_ms",
        "startup_timeout_ms",
    }
)
INTEGER_FIELDS = BUDGET_FIELDS | {
    "idempotency_pool_max",
    "telemetry_shutdown_ms",
    "health_probe_budget_ms",
    "otlp_timeout_ms",
    "log_queue_capacity",
    "trace_queue_capacity",
}


def environment_key(name: str) -> str:
    return "DATABASE_URL" if name == "database_url" else "PAYMENTS_" + name.upper()


def load_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Migration-compatible literal dotenv/environment policy, without process mutation."""
    if env is not None:
        return dict(env)
    return {
        k: v for k, v in dotenv_values(ENV_FILE, interpolate=False).items() if v is not None
    } | dict(os.environ)


def known_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: values[key]
        for name in Settings.model_fields
        if (key := environment_key(name)) in values
    }


class AliasSource(PydanticBaseSettingsSource):
    """Only declared aliases from a flat source; no interpolation or ambient fallback."""

    def __init__(self, settings_cls: type[BaseSettings], values: Callable[[], Mapping[str, Any]]):
        super().__init__(settings_cls)
        self.values = values

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        key = environment_key(field_name)
        return self.values().get(key), key, False

    def __call__(self) -> dict[str, Any]:
        values = self.values()
        return {
            key: values[key]
            for name in self.settings_cls.model_fields
            if (key := environment_key(name)) in values
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        extra="forbid",
        alias_generator=environment_key,
        populate_by_name=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    database_url: str = Field(min_length=1, repr=False)
    profile: Literal["local", "configured"] = "configured"
    db_connection_budget: int = Field(default=8, ge=1)
    max_processes: int = Field(default=2, ge=1)
    pool_min: int = Field(default=1, ge=0)
    pool_max: int = Field(default=4, ge=2)
    pool_max_waiting: int = Field(default=4, ge=1)
    idempotency_pool_max: int = Field(default=1, ge=1)
    acquire_timeout_ms: int = Field(default=1000, ge=1)
    connect_timeout_seconds: int = Field(default=2, ge=1)
    statement_timeout_ms: int = Field(default=3000, ge=1)
    lock_timeout_ms: int = Field(default=2000, ge=1)
    idle_transaction_timeout_ms: int = Field(default=5000, ge=1)
    transaction_timeout_ms: int = Field(default=10000, ge=1)
    operation_budget_ms: int = Field(default=15000, ge=1)
    response_margin_ms: int = Field(default=1000, ge=1)
    startup_timeout_ms: int = Field(default=10000, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    telemetry_shutdown_ms: int = Field(default=200, ge=1, le=100000)
    health_probe_budget_ms: int = Field(default=1000, ge=1, le=100000)
    otlp_timeout_ms: int = Field(default=200, ge=1, le=100000)
    log_queue_capacity: int = Field(default=256, ge=1, le=100000)
    trace_queue_capacity: int = Field(default=512, ge=1, le=100000)
    trace_sample_ratio: float = Field(default=1, ge=0, le=1)
    otlp_endpoint: str | None = Field(default=None, repr=False)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        if _ISOLATED.get():
            return (init_settings,)
        return (
            init_settings,
            AliasSource(settings_cls, lambda: os.environ),
            AliasSource(
                settings_cls,
                lambda: {
                    k: v
                    for k, v in dotenv_values(ENV_FILE, interpolate=False).items()
                    if v is not None
                },
            ),
        )

    @field_validator(*sorted(INTEGER_FIELDS), mode="before")
    @classmethod
    def exact_integer(cls, value: object) -> int:
        if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value):
            return int(value)
        if type(value) is not int:
            raise ValueError("integer required")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("otlp_endpoint", mode="before")
    @classmethod
    def optional_endpoint(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_budgets(self) -> Self:
        if self.profile == "configured" and (missing := BUDGET_FIELDS - self.model_fields_set):
            raise ValueError(
                "Explicit deployment budget required: " + environment_key(sorted(missing)[0])
            )
        if (
            self.pool_min > self.pool_max
            or self.max_processes * self.pool_max > self.db_connection_budget
        ):
            raise ValueError("Pool/process counts exceed the declared connection budget")
        if self.idempotency_pool_max >= self.pool_max:
            raise ValueError("Lifecycle and business pools both require capacity")
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
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        if env is None:
            return cls._validated({})
        return cls._isolated(known_values(env))

    @classmethod
    def local(cls, database_url: str, **overrides: Any) -> Self:
        return cls._isolated(dict(database_url=database_url, profile="local", **overrides))

    @classmethod
    def _isolated(cls, values: dict[str, Any]) -> Self:
        token = _ISOLATED.set(True)
        try:
            return cls._validated(values)
        finally:
            _ISOLATED.reset(token)

    @classmethod
    def _validated(cls, values: dict[str, Any]) -> Self:
        try:
            return cls(**values)
        except ValidationError as error:
            # Never include raw inputs, URLs, or exception contexts in startup diagnostics.
            errors = error.errors(include_input=False, include_context=False)
            if any(
                e["msg"].startswith("Value error, Explicit deployment budget required:")
                for e in errors
            ):
                raise ValueError("Explicit deployment budget required") from None
            raise ValueError(
                "Invalid service configuration: integer, budget or field validation failed"
            ) from None
