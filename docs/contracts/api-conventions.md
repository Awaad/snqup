# API Conventions

Binding on every endpoint. Deviations require an ADR.

## Versioning

All routes are under `/v1/`. Mobile clients cannot be force-updated, so we will run `/v1`
for years. A `/v2` is only created for a genuinely breaking change; additive changes stay
in `/v1`.

## What is breaking

Breaking, requires a new version:
- Removing a field, or making an optional field required
- Changing a field's type or making a nullable field non-nullable
- Removing an enum value the client may already send
- Changing an error code's meaning

Not breaking, ship in `/v1`:
- Adding an optional request field
- Adding a response field
- Adding a new enum value **that the server may return** — clients must tolerate unknown
  enum values on read. This is a client requirement, stated here so it is testable.

## Identifiers in responses

**Internal UUIDs are never exposed on public (unauthenticated) surfaces.** A UUIDv7 leaks
its creation timestamp (ADR-0010). Public responses carry opaque tokens or slugs only.

Authenticated responses may carry internal IDs for the caller's own resources.

## Naming

- Paths: plural nouns, kebab-case. `/v1/connection-views`
- JSON fields: `snake_case`, matching the database and Pydantic. The generated TypeScript
  client preserves this; do not add a camelCase transform layer, because it breaks the
  one-to-one mapping that makes the generated types trustworthy.
- Timestamps: RFC 3339, always UTC, always suffixed `_at`.
- Booleans: `is_` or `has_` prefix.

## Pagination

Cursor-based everywhere. Never offset — offset pagination on a growing table skips and
duplicates rows as data is inserted.

```json
{
  "items": [],
  "next_cursor": "opaque-string-or-null"
}
```

The cursor encodes the sort key and the last ID. It is opaque to clients.

## Idempotency

**Every mutating request carries `Idempotency-Key`**, a client-generated UUIDv7.
Stored 24 hours in Valkey. A repeat with the same key returns the original response
without re-executing.

This is mandatory, not optional: offline retry (ADR-0016) means duplicates are the normal
case, not an edge case.

## Errors

Uniform envelope. The backend returns **codes, never user-facing strings** (ADR-0011).

```json
{
  "error": {
    "code": "CARD_LIMIT_REACHED",
    "message": "User has 1 card, entitlement allows 1",
    "details": { "limit": 1, "current": 1 },
    "request_id": "01J..."
  }
}
```

`message` is **developer-facing only** and must never be displayed to a user. Clients
render text from their own locale files keyed by `code`. Registry: `error-codes.md`.

## Request ID

Originates at Cloudflare, propagates through every service in `X-Request-ID`, and is
returned in every response including errors. One identifier maps a user's report to a trace
across three runtimes (ADR-0024).

## Authentication

`Authorization: Bearer <jwt>`. Verified in FastAPI behind the provider adapter
(`auth/provider.py`). The JWT `sub` maps to `users.auth_subject`.

Public endpoints are explicitly listed in one place, never inferred from the absence of a
decorator. Default is authenticated; opting out is deliberate and visible.

## Rate limiting

Communicated via headers on every response:

```
RateLimit-Limit, RateLimit-Remaining, RateLimit-Reset
```

`429` returns `Retry-After`. **Scan endpoints fail open with logging** rather than
blocking (ADR-0007) — a false positive at a live event is worse than an unthrottled hour.

## Codegen

FastAPI emits `openapi.json` from Pydantic models. `openapi-typescript` generates
`packages/api-client`. CI regenerates and **fails the build if the committed output
differs** (ADR-0014).

Generated files carry a header marking them generated and are excluded from review.
Never hand-edit them.

## Health

- `/health` — liveness. No dependencies. Used by uptime monitoring.
- `/health/ready` — readiness. Checks Postgres and Valkey. Used by the deploy gate.

`/health` must never touch the database. A database blip should not restart the process.
