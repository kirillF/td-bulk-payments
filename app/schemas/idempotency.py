"""Validation of durable result envelopes, independent of request execution."""

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
