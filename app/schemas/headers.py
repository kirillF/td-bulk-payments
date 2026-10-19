"""Protocol value validation independent of HTTP extraction."""

from typing import Annotated

from pydantic import BaseModel, Field

type KeyValue = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")]


class IdempotencyHeaders(BaseModel):
    keys: list[KeyValue] = Field(min_length=1, max_length=1)


def correlation_id(headers: list[tuple[bytes, bytes]]) -> str:
    """One bounded opaque token; malformed/ambiguous input is replaced, never echoed."""
    import re
    from uuid import uuid4

    values = [value for name, value in headers if name.lower() == b"x-correlation-id"]
    if len(values) == 1 and re.fullmatch(rb"[A-Za-z0-9._-]{1,128}", values[0]):
        return values[0].decode("ascii")
    return uuid4().hex
