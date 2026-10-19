"""Application construction and resource lifecycle."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware.idempotency import idempotency_middleware
from app.api.router import install_routes
from app.core.config import Settings
from app.core.observability import RequestTelemetryMiddleware, Telemetry
from app.db.database import create_pool, run_owned, validate_revision


def create_app(configuration: Settings | str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = (
            Settings.local(configuration)
            if isinstance(configuration, str)
            else configuration or Settings.from_env()
        )
        application.state.settings = settings
        pool = create_pool(settings)
        telemetry = Telemetry(settings=settings)
        application.state.telemetry = telemetry
        try:
            async with asyncio.timeout(settings.startup_timeout_ms / 1000):
                await pool.open(wait=True, timeout=settings.startup_timeout_ms / 1000)
                await run_owned(
                    pool,
                    validate_revision,
                    settings.startup_timeout_ms,
                    settings.response_margin_ms,
                )
            application.state.pool = pool
            yield
        finally:
            await pool.close(timeout=settings.startup_timeout_ms / 1000)
            telemetry.close()

    application = FastAPI(title="Firm Payments", version="0.1.0", lifespan=lifespan)

    application.middleware("http")(idempotency_middleware)
    application.add_middleware(RequestTelemetryMiddleware, owner=application)
    install_routes(application)
    return application


app = create_app()
