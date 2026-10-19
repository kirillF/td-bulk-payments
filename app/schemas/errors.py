"""Documented HTTP error and validation response schemas."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str
    input: object | None = None
    ctx: dict[str, object] | None = None


class ValidationResponse(BaseModel):
    detail: list[ValidationIssue]
