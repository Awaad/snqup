"""Identity provider adapter (ADR-0005).

The ONLY module that knows which IdP we use. Supabase Auth holds credentials
and issues JWTs; it never sees a card, a connection or an event. Swapping
providers touches this file and nothing else.

Multiple public keys are accepted at once because rotation is two-phase: the
new key must be accepted before it signs anything, or rotation is an outage
(runbooks/secret-rotation.md).
"""

import asyncio
import time
from typing import Any, Protocol

import httpx
import jwt

from acme.core.errors import ApiError


class TokenVerifier(Protocol):
    """Verification is ASYNC because JWKS needs a network fetch.

    Making it sync would force the JWKS verifier to block the event loop on
    every cache miss, which at startup is every request at once.
    """

    async def verify(self, token: str) -> dict[str, Any]: ...


class JwtVerifier:
    def __init__(self, *, public_keys: list[str], issuer: str, audience: str) -> None:
        self._public_keys = public_keys
        self._issuer = issuer
        self._audience = audience

    async def verify(self, token: str) -> dict[str, Any]:
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


class JwksVerifier:
    """Verify against a remote JWKS. This is how Supabase Auth actually works.

    Supabase rotates signing keys and publishes them at
    `{issuer}/.well-known/jwks.json`. Pinning a static public key means the
    next rotation logs every user out at once, with no warning and no way to
    fix it without a deploy.

    CACHING is not an optimisation here. Fetching JWKS on every request puts
    Supabase's availability directly in the path of every authenticated call,
    so an outage there becomes a total outage here. The cache is what makes
    that a degradation instead.

    A cache MISS on an unknown `kid` triggers exactly one refetch, because a
    key genuinely can be new. Refetching on every unknown kid without that
    guard turns a stream of garbage tokens into a denial-of-service against the
    IdP, which then rate-limits us.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        cache_seconds: int = 600,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url or f"{issuer.rstrip('/')}/.well-known/jwks.json"
        self._cache_seconds = cache_seconds
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._keys and now - self._fetched_at < self._cache_seconds:
            return

        async with self._lock:
            # Re-check inside the lock: a burst of requests on a cold cache
            # would otherwise all fetch, which is the thundering herd the lock
            # exists to prevent.
            now = time.monotonic()
            if not force and self._keys and now - self._fetched_at < self._cache_seconds:
                return

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self._jwks_url)
                response.raise_for_status()
                document = response.json()

            self._keys = {
                key["kid"]: jwt.PyJWK(key) for key in document.get("keys", []) if "kid" in key
            }
            self._fetched_at = time.monotonic()

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise ApiError(
                "AUTH_TOKEN_INVALID", status_code=401, message="malformed token"
            ) from exc

        kid = header.get("kid")
        if not kid:
            raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="token has no kid")

        await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            # One retry, and only for an unknown kid: a rotation is legitimate,
            # a flood of garbage kids is not.
            await self._refresh(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="unknown signing key")

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=[header.get("alg", "RS256")],
                issuer=self._issuer,
                audience=self._audience,
            )
        except jwt.ExpiredSignatureError as exc:
            raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="expired") from exc
        except jwt.InvalidTokenError as exc:
            raise ApiError(
                "AUTH_TOKEN_INVALID", status_code=401, message="verification failed"
            ) from exc

        return claims


class SharedSecretVerifier:
    """HS256 against Supabase's legacy JWT secret.

    NECESSARY, not legacy-tolerance. Supabase projects come in two shapes and
    the difference is invisible until a token arrives:

      asymmetric keys   ES256/RS256, published at /auth/v1/.well-known/jwks.json.
                        The default for new projects, and what JwksVerifier
                        handles.
      legacy JWT secret HS256 with a SHARED SECRET. There is no JWKS endpoint
                        at all, so JwksVerifier fails on every request with
                        "unknown signing key" - which reads like a key rotation
                        problem and is not.

    A shared secret means anyone holding it can MINT tokens, not merely verify
    them. It must never leave the server, and rotating to asymmetric keys is
    worth doing before launch.
    """

    def __init__(self, *, secret: str, issuer: str, audience: str) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
            )
        except jwt.ExpiredSignatureError as exc:
            raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="expired") from exc
        except jwt.InvalidTokenError as exc:
            raise ApiError(
                "AUTH_TOKEN_INVALID", status_code=401, message="verification failed"
            ) from exc
        return claims


def build_verifier(settings: Any) -> TokenVerifier:
    """Pick a verifier from configuration.

    Three modes, and picking the wrong one fails on EVERY request rather than
    intermittently, which is at least loud:

      jwt_public_keys   static keys. Tests and local development only.
      jwt_shared_secret HS256. Supabase projects still on the legacy JWT
                        secret, which have NO JWKS endpoint.
      otherwise         JWKS. The default for new Supabase projects, and the
                        only one that survives key rotation without a deploy.
    """
    if settings.jwt_public_keys:
        return JwtVerifier(
            public_keys=settings.jwt_public_keys,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    if settings.jwt_shared_secret:
        return SharedSecretVerifier(
            secret=settings.jwt_shared_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    return JwksVerifier(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwt_jwks_url or None,
    )
