"""Endpoints that are deliberately unauthenticated.

Listed in ONE place rather than inferred from the absence of a decorator. An
endpoint that is public because someone forgot something is the failure this
prevents, and a list is reviewable in a diff in a way that an absence is not.

Adding a path here should be a conscious moment in code review.
"""

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/health/ready",
        # FastAPI's own schema surfaces. Listed so the audit in
        # tests/test_api_surface.py has nothing to special-case, and because
        # their being public is a real decision even if a narrow one.
        #
        # All three are None in production (main.py). They were NOT: docs_url
        # was disabled and redoc_url was never set, so /redoc served every
        # endpoint, schema and field name to anyone who guessed the path.
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        # Scan resolution and the public card page. Anonymous by design: most
        # scanners have no account, and that is the growth loop, not an edge
        # case (ADR-0008).
        "/v1/scan/{token}",
        "/v1/scan/{scan_id}/interactions",
        "/v1/scan/{scan_id}/reply",
        "/v1/cards/public/{slug}",
        # Billing webhooks authenticate by signature, not by bearer token.
        # All three authenticate by their own mechanism rather than a bearer
        # token: HMAC (Stripe), signed JWS (Apple), Pub/Sub OIDC (Google Play).
        "/v1/webhooks/stripe",
        "/v1/webhooks/apple",
        "/v1/webhooks/google-play",
    }
)
