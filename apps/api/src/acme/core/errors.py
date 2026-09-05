"""Error envelope (contracts/api-conventions.md).

The API returns CODES, never user-facing strings (ADR-0011). Clients render
text from their own locale files. `message` is developer-facing and must never
be displayed to a user.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import ORJSONResponse


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message
        self.details = details or {}
        super().__init__(code)


async def api_error_handler(request: Request, exc: ApiError) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
