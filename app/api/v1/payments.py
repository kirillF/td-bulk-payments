"""Version-one bulk payment HTTP controller."""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

from app.core.observability import attempt_result
from app.db.database import CommitUncertain, OperationDeadlineExceeded, run_transaction
from app.schemas.errors import ErrorResponse, ValidationResponse
from app.schemas.headers import KeyValue
from app.schemas.payments import BulkPayment, BulkPaymentResponse
from app.services.idempotency import StoredResultInvalid, execute

router = APIRouter()


def single_key(values: Annotated[list[KeyValue], Header(alias="Idempotency-Key")]) -> str:
    if len(values) != 1:
        raise RequestValidationError(
            [
                {
                    "loc": ("header", "Idempotency-Key"),
                    "msg": "Exactly one Idempotency-Key header is required",
                    "type": "value_error",
                }
            ]
        )
    return values[0]


@router.post(
    "/bulk-payments",
    status_code=201,
    response_model=BulkPaymentResponse,
    responses={
        422: {
            "model": ErrorResponse | ValidationResponse,
            "description": "Request validation or business rejection",
        },
        409: {"model": ErrorResponse, "description": "Key reused for a different request"},
        503: {
            "model": ErrorResponse,
            "description": "Retry the same key",
            "headers": {
                "Retry-After": {
                    "schema": {"type": "string"},
                    "description": "Retry delay in seconds",
                }
            },
        },
    },
)
async def bulk_payments(
    request: Request, body: BulkPayment, idempotency_key: Annotated[str, Depends(single_key)]
) -> JSONResponse:
    try:
        result = await run_transaction(
            request.app.state.pool,
            lambda connection: execute(connection, body, idempotency_key),
        )
    except (
        psycopg.Error,
        PoolTimeout,
        TooManyRequests,
        PoolClosed,
        StoredResultInvalid,
        OperationDeadlineExceeded,
        CommitUncertain,
    ) as error:
        # A commit acknowledgement can be lost: retry the same key, never a
        # new one. PostgreSQL either replays the commit or executes once.
        outcome = (
            "unknown_commit"
            if isinstance(error, CommitUncertain)
            else "invariant_breach"
            if isinstance(error, StoredResultInvalid)
            else "transient_failure"
        )
        attempt_result(outcome, error=error)
        return JSONResponse(
            status_code=503,
            content={"detail": "service_unavailable"},
            headers={"Retry-After": "1"},
        )
    attempt_result(
        result.outcome, result.body["payments_created"] if result.outcome == "committed" else 0
    )
    return JSONResponse(
        status_code={"accepted": 201, "rejected": 422, "conflict": 409}[result.kind],
        content=result.body,
    )
