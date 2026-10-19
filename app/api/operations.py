"""Unversioned health and metrics controllers."""

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from psycopg_pool import PoolClosed, PoolTimeout, TooManyRequests

from app.core.logging import stage
from app.core.observability import attempt_result
from app.db.database import Connection, is_ready, run_owned

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    telemetry = request.app.state.telemetry
    pool = request.app.state.pool

    async def probe(connection: Connection) -> bool:
        with stage("health.probe"):
            return await is_ready(connection)

    try:
        available = await run_owned(
            pool,
            probe,
            telemetry.health_budget_ms,
            min(pool.settings.response_margin_ms, telemetry.health_budget_ms // 4),
        )
    except (psycopg.Error, PoolTimeout, TooManyRequests, PoolClosed, TimeoutError) as error:
        attempt_result("transient_failure", error=error)
        available = False
    telemetry.readiness(available)
    return JSONResponse(
        {"status": "ready" if available else "not_ready"}, status_code=200 if available else 503
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return Response(
        request.app.state.telemetry.metrics(request.app.state.pool),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
