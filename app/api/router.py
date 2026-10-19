"""Public route composition."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.errors import invalid_request
from app.api.operations import router as operations_router
from app.api.v1.payments import router as payments_router


def install_routes(application: FastAPI) -> None:
    application.add_exception_handler(RequestValidationError, invalid_request)
    application.include_router(operations_router)
    application.include_router(payments_router, prefix="/v1")
