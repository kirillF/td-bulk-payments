"""Protocol value validation independent of HTTP extraction."""

from typing import Annotated

from pydantic import BaseModel, Field

type KeyValue = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")]


class IdempotencyHeaders(BaseModel):
    keys: list[KeyValue] = Field(min_length=1, max_length=1)
