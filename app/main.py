"""Application construction and resource lifecycle."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import install_routes
from app.core.config import Settings
from app.core.observability import RequestTelemetryMiddleware, Telemetry
from app.db.database import AcquisitionBudget, create_pool, run_owned, validate_schema


def create_app(configuration: Settings | str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = (
            Settings.local(configuration)
            if isinstance(configuration, str)
            else configuration or Settings.from_env()
        )
        application.state.settings = settings
        admission = AcquisitionBudget(settings.pool_max_waiting)
        pool = create_pool(settings, role="business", admission=admission)
        lifecycle = create_pool(settings, role="lifecycle", admission=admission)
        pool.peers = lifecycle.peers = (pool, lifecycle)
        telemetry = Telemetry(settings=settings)
        application.state.telemetry = telemetry
        try:
            async with asyncio.timeout(settings.startup_timeout_ms / 1000):
                await lifecycle.open(wait=True, timeout=settings.startup_timeout_ms / 1000)
                await pool.open(wait=True, timeout=settings.startup_timeout_ms / 1000)
                await run_owned(
                    pool, validate_schema, settings.startup_timeout_ms, settings.response_margin_ms
                )
            application.state.pool = pool
            application.state.lifecycle_pool = lifecycle
            yield
        finally:
            await asyncio.gather(
                pool.close(timeout=settings.startup_timeout_ms / 1000),
                lifecycle.close(timeout=settings.startup_timeout_ms / 1000),
            )
            telemetry.close()

    application = FastAPI(title="Firm Payments", version="0.1.0", lifespan=lifespan)

    application.add_middleware(RequestTelemetryMiddleware, owner=application)
    install_routes(application)
    return application


app = create_app()
