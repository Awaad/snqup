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
        "/openapi.json",
        "/docs",
        # Scan resolution and the public card page. Anonymous by design: most
        # scanners have no account, and that is the growth loop, not an edge
        # case (ADR-0008).
        "/v1/scan/{token}",
        "/v1/scan/{scan_id}/saved",
        "/v1/scan/{scan_id}/reply",
        "/v1/cards/public/{slug}",
        # Billing webhooks authenticate by signature, not by bearer token.
        "/v1/webhooks/stripe",
        "/v1/webhooks/apple",
    }
)
