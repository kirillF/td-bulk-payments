"""Validation of durable result envelopes, independent of request execution."""

import json
from typing import Any

from pydantic import ValidationError

from app.schemas.payments import BulkPayment, BulkPaymentResponse


class StoredResultInvalid(RuntimeError):
    """A corrupt committed result must never authorize another execution."""


def validate_result(status: int, body: Any, request: BulkPayment | None = None) -> dict[str, Any]:
    try:
        if status == 201:
            response = BulkPaymentResponse.model_validate(body)
            if request is not None and (
                len(response.payments) != len(request.payments)
                or any(
                    actual.payer_firm_uuid != request.payer_firm_uuid
                    or actual.payee_firm_uuid != expected.payee_firm_uuid
                    or actual.amount_cents != expected.amount_cents
                    or actual.description != expected.description
                    for actual, expected in zip(response.payments, request.payments, strict=True)
                )
            ):
                raise StoredResultInvalid("stored result does not match request")
            return response.model_dump(mode="json")
        if (
            status == 422
            and isinstance(body, dict)
            and set(body) == {"detail"}
            and body["detail"] in {"unknown_firm", "insufficient_funds", "balance_limit_exceeded"}
        ):
            return body
    except (ValidationError, TypeError, ValueError) as error:
        raise StoredResultInvalid("stored result is invalid") from error
    raise StoredResultInvalid("stored result is incomplete")


def prepare_request(body: bytes, keys: list[str]) -> tuple[BulkPayment, str]:
    """Validate protocol inputs once, retaining safe HTTP error locations."""
    from fastapi.exceptions import RequestValidationError

    from app.schemas.headers import IdempotencyHeaders

    try:
        header = IdempotencyHeaders(keys=keys)
    except ValidationError as error:
        raise RequestValidationError(
            [
                {**item, "loc": ("header", "Idempotency-Key", *item["loc"][1:])}
                for item in error.errors()
            ]
        ) from None
    try:
        decoded = json.loads(body)
    except ValueError, UnicodeDecodeError, RecursionError:
        raise RequestValidationError(
            [{"loc": ("body",), "msg": "Invalid JSON", "type": "json_invalid"}]
        ) from None
    try:
        request = BulkPayment.model_validate(decoded)
    except ValidationError as error:
        raise RequestValidationError(
            [{**item, "loc": ("body", *item["loc"])} for item in error.errors()]
        ) from None
    return request, header.keys[0]
