"""Identity provider adapter (ADR-0005).

The ONLY module that knows which IdP we use. Supabase Auth holds credentials
and issues JWTs; it never sees a card, a connection or an event. Swapping
providers touches this file and nothing else.

Multiple public keys are accepted at once because rotation is two-phase: the
new key must be accepted before it signs anything, or rotation is an outage
(runbooks/secret-rotation.md).
"""

from typing import Any, Protocol

import jwt

from acme.core.errors import ApiError


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict[str, Any]: ...


class JwtVerifier:
    def __init__(self, *, public_keys: list[str], issuer: str, audience: str) -> None:
        self._public_keys = public_keys
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for key in self._public_keys:
            try:
                claims: dict[str, Any] = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256", "ES256"],
                    issuer=self._issuer,
                    audience=self._audience,
                )
                return claims
            except jwt.InvalidTokenError as exc:
                last_error = exc
                continue

        if isinstance(last_error, jwt.ExpiredSignatureError):
            raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="expired")
        raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="verification failed")
