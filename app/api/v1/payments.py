"""Version-one controller: orchestrate monetary work and its atomic receipt."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.logging import instrument
from app.core.observability import CURRENT, attempt_result
from app.db.database import Connection, run_transaction
from app.models.idempotency import Admission, StoredResponse
from app.schemas.errors import ErrorResponse, ValidationResponse
from app.schemas.idempotency import validate_result
from app.schemas.payments import BulkPayment, BulkPaymentResponse
from app.services.idempotency import acquire_execution_guard, store_receipt
from app.services.payments import BulkPaymentService

router = APIRouter()


def request_schema() -> dict[str, Any]:
    schema = BulkPayment.model_json_schema()
    schema["properties"]["payments"]["items"] = schema.pop("$defs")["Payment"]
    return schema


@instrument("controller.execute")
async def execute_admitted(
    connection: Connection, body: BulkPayment, admission: Admission
) -> StoredResponse:
    await acquire_execution_guard(connection, admission)
    result = await BulkPaymentService.transfer(connection, body)
    status: Literal[201, 422] = 201 if result.kind == "accepted" else 422
    response = StoredResponse(status, validate_result(status, result.body, body))
    await store_receipt(connection, admission, response)
    context = CURRENT.get()
    if context is not None:
        context.commit_candidate = result.kind == "accepted"
    return response


@router.post(
    "/bulk-payments",
    status_code=201,
    response_model=BulkPaymentResponse,
    responses={
        422: {"model": ErrorResponse | ValidationResponse},
        409: {"model": ErrorResponse, "description": "Request processing or conflicting key"},
        503: {
            "model": ErrorResponse,
            "description": "Retry the same key",
            "headers": {"Retry-After": {"schema": {"type": "string"}}},
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema()}},
        },
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": r"^[\x21-\x7e]+$",
                },
            }
        ],
    },
)
async def bulk_payments(request: Request) -> JSONResponse:
    body: BulkPayment = request.state.prepared_payment
    admission: Admission = request.state.admission
    result = await run_transaction(
        request.app.state.pool, lambda connection: execute_admitted(connection, body, admission)
    )
    request.state.payment_result = result
    attempt_result(
        "committed" if result.status_code == 201 else "business_rejected",
        result.body["payments_created"] if result.status_code == 201 else 0,
    )
    return JSONResponse(status_code=result.status_code, content=result.body)
