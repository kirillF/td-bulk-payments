"""FastAPI HTTP middleware for request admission and durable result publication."""

from collections.abc import Awaitable, Callable

import psycopg
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

from app.api.errors import invalid_request
from app.core.observability import attempt_result
from app.db.database import (
    CommitUncertain,
    OperationDeadlineExceeded,
    operation_scope,
    run_transaction,
)
from app.db.idempotency import publish_completed, release_failed
from app.models.idempotency import Admission, AdmissionLost, Completed, Conflict
from app.schemas.idempotency import StoredResultInvalid, prepare_request, validate_result
from app.services.idempotency import check


async def idempotency_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method != "POST" or request.url.path != "/v1/bulk-payments":
        return await call_next(request)
    try:
        async with operation_scope(request.app.state.pool):
            body, key = prepare_request(
                await request.body(), request.headers.getlist("Idempotency-Key")
            )
            request.state.prepared_payment = body
            decision = await run_transaction(
                request.app.state.pool,
                lambda connection: check(
                    connection,
                    str(body.payer_firm_uuid),
                    key,
                    body.fingerprint(),
                    request.app.state.settings.operation_budget_ms,
                ),
            )
            if isinstance(decision, Completed):
                result = decision.response
                content = validate_result(result.status_code, result.body, body)
                attempt_result("replayed")
                return JSONResponse(status_code=result.status_code, content=content)
            if not isinstance(decision, Admission):
                conflict = isinstance(decision, Conflict)
                attempt_result("conflict")
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "idempotency_key_conflict" if conflict else "request_processing"
                    },
                )
            request.state.admission = decision
            try:
                response = await call_next(request)
            except Exception as error:
                # call_next has joined the controller's supervised DB task. A lost
                # commit acknowledgement is never proof of rollback.
                if not isinstance(error, CommitUncertain):
                    try:
                        await run_transaction(
                            request.app.state.pool,
                            lambda connection: release_failed(connection, decision),
                        )
                    except psycopg.Error, TimeoutError, PoolTimeout, TooManyRequests, PoolClosed:
                        pass  # Retain processing when bounded cleanup cannot establish safety.
                raise
            # Only the controller's schema-validated durable result can be published.
            # Never capture or cache arbitrary HTTP responses/streaming bodies.
            if not hasattr(request.state, "payment_result"):
                raise StoredResultInvalid("Admitted controller produced no durable result")
            await run_transaction(
                request.app.state.pool, lambda connection: publish_completed(connection, decision)
            )
            return response
    except RequestValidationError as error:
        attempt_result("validation_rejected")
        return await invalid_request(request, error)
    except (
        psycopg.Error,
        PoolTimeout,
        TooManyRequests,
        PoolClosed,
        StoredResultInvalid,
        AdmissionLost,
        OperationDeadlineExceeded,
        TimeoutError,
    ) as error:
        outcome = (
            "unknown_commit"
            if isinstance(error, CommitUncertain)
            else "invariant_breach"
            if isinstance(error, StoredResultInvalid)
            else "transient_failure"
        )
        attempt_result(outcome, error=error)
        return JSONResponse(
            status_code=503, content={"detail": "service_unavailable"}, headers={"Retry-After": "1"}
        )
