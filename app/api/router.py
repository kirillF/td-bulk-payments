"""Public route composition."""

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.types import Scope

from app.api.errors import invalid_request
from app.api.operations import router as operations_router
from app.api.route_metadata import RouteComposition
from app.api.v1.payments import router as payments_router
from app.core.request_context import RequestRoute


def install_routes(application: FastAPI) -> Callable[[Scope], RequestRoute]:
    application.add_exception_handler(RequestValidationError, invalid_request)
    composition = RouteComposition(application)
    composition.include(operations_router)
    composition.include(payments_router, prefix="/v1")
    return composition.freeze()
