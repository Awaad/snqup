"""Idempotency middleware.

Applies the contract from `contracts/api-conventions.md` to every mutating
request, rather than leaving each endpoint to remember.

MIDDLEWARE, not a dependency, for one reason: a dependency runs before the
handler and cannot see the response. Replaying a stored response requires
wrapping the whole call.

SCOPE. Only authenticated mutations. Webhooks are excluded because providers
have their own dedup (billing_events), and public scan writes are excluded
because they have no user to scope a key to - an unauthenticated caller could
otherwise poison another request's key.
"""

import contextlib
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from acme.core.idempotency import MUTATING_METHODS, IdempotencyStore, StoredResponse, fingerprint

log = structlog.get_logger()

EXEMPT_PREFIXES = ("/v1/webhooks", "/v1/scan", "/health")


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        key = request.headers.get("Idempotency-Key")
        if (
            key is None
            or request.method not in MUTATING_METHODS
            or request.url.path.startswith(EXEMPT_PREFIXES)
        ):
            return await call_next(request)

        # Reading the body here consumes the stream, so it is put back before
        # the handler runs. Without this the endpoint receives an empty body
        # and every idempotent request fails validation - which would look like
        # a bug in the endpoint, not in this middleware.
        body = await request.body()

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)

        # Scoped by the raw Authorization header rather than the resolved user:
        # auth has not run yet at middleware level. Hashing it keeps the token
        # out of Redis keys.
        import hashlib

        auth = request.headers.get("Authorization", "")
        subject = hashlib.sha256(auth.encode()).hexdigest()[:32] if auth else "anon"

        store = IdempotencyStore(request.app.state.redis)
        request_fingerprint = fingerprint(request.method, request.url.path, body)

        try:
            replay = await store.begin(subject, key, request_fingerprint)
        except KeyError:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "message": "key already used for a different request",
                        "details": {},
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
            )
        except RuntimeError:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "message": "original request still in flight; retry shortly",
                        "details": {},
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
                headers={"Retry-After": "2"},
            )
        except Exception as exc:
            # FAIL OPEN, like rate limiting (ADR-0007). Valkey being down must
            # degrade duplicate protection, not stop people exchanging cards at
            # an event - the unique index still catches the important case.
            log.warning("idempotency.unavailable", error=str(exc))
            return await call_next(request)

        if replay is not None:
            response = Response(
                content=replay.body,
                status_code=replay.status_code,
                media_type="application/json",
            )
            # So a client can tell a replay from a fresh execution, which
            # matters when debugging a sync that "worked twice".
            response.headers["Idempotency-Replayed"] = "true"
            return response

        try:
            response = await call_next(request)
        except Exception:
            # Release the claim, or one transient error blocks every retry of
            # that operation for 24 hours.
            with contextlib.suppress(Exception):
                await store.release(subject, key)
            raise

        if 200 <= response.status_code < 300:
            payload = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                await store.complete(
                    subject,
                    key,
                    request_fingerprint,
                    StoredResponse(response.status_code, payload),
                )
            return Response(
                content=payload,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        # Non-2xx: release so the client can retry. Caching a 500 would pin a
        # transient failure for a day.
        with contextlib.suppress(Exception):
            await store.release(subject, key)
        return response
