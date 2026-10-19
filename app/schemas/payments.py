"""Request validation and the canonical representation used for idempotency."""

import hashlib
import json
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

MAX_CENTS = 2_147_483_647  # The exercise specifies PostgreSQL INTEGER columns.


class Payment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: StrictStr = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$", max_length=32)
    payee_firm_uuid: UUID
    description: StrictStr = Field(max_length=2000)

    @field_validator("description", mode="before")
    @classmethod
    def sanitize_description(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return "".join(c for c in value if c != "\x00" and not "\ud800" <= c <= "\udfff")

    @field_validator("amount")
    @classmethod
    def normalize_amount(cls, value: str) -> str:
        dollars, _, fraction = value.partition(".")
        cents = int(dollars) * 100 + int(fraction.ljust(2, "0"))
        if not 0 < cents <= MAX_CENTS:
            raise ValueError("amount must be positive and fit a signed 32-bit cents value")
        return f"{cents // 100}.{cents % 100:02d}"

    @property
    def amount_cents(self) -> int:
        return int(self.amount.replace(".", ""))


class BulkPayment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payer_firm_uuid: UUID
    payments: list[Payment] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def reject_self_payments(self) -> Self:
        if any(payment.payee_firm_uuid == self.payer_firm_uuid for payment in self.payments):
            raise ValueError("payer cannot also be a payee")
        return self

    @property
    def total_cents(self) -> int:
        return sum(payment.amount_cents for payment in self.payments)

    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(("v1:" + canonical).encode()).hexdigest()


class PaymentCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(gt=0)
    payer_firm_uuid: UUID
    payee_firm_uuid: UUID
    amount_cents: int = Field(gt=0, le=MAX_CENTS)
    description: str


class BulkPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payments_created: int = Field(gt=0)
    total_amount_cents: int = Field(gt=0)
    payments: list[PaymentCreated]

    @model_validator(mode="after")
    def consistent_totals(self) -> Self:
        if self.payments_created != len(self.payments):
            raise ValueError("payment count mismatch")
        if self.total_amount_cents != sum(p.amount_cents for p in self.payments):
            raise ValueError("payment total mismatch")
        if len({p.id for p in self.payments}) != len(self.payments):
            raise ValueError("duplicate payment ID")
        return self
