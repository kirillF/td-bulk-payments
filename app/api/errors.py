"""Safe protocol validation-error serialization."""

import json

from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response


async def invalid_request(_request: object, error: Exception) -> Response:
    assert isinstance(error, RequestValidationError)
    # Arbitrary rejected input/context may contain unencodable Unicode.
    # The stable validation contract needs only location, message and type.
    return Response(
        status_code=422,
        media_type="application/json",
        content=json.dumps(
            {
                "detail": [
                    {name: item[name] for name in ("loc", "msg", "type")} for item in error.errors()
                ]
            },
            ensure_ascii=True,
        ),  # Location keys can themselves contain rejected Unicode.
    )
