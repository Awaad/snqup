# ADR-0010: UUIDv7 internally, opaque tokens publicly

**Status:** Accepted
**Date:** 2026-09-04

## Context

Three distinct identifier needs, often conflated: internal primary keys, public
capability tokens, and short links.

UUIDv4 primary keys scatter B-tree inserts randomly across the index. On an append-only
table like `connections`, that means constant page splits and poor index locality. Serial
integers avoid that but leak volume and enable enumeration.

Public tokens have different requirements again: they must not leak metadata, must be
revocable, and must be short, because every character increases QR module count and
therefore reduces scan reliability.

## Decision

**Internal primary keys: UUIDv7, generated in the application.**

v7 is time-ordered, so inserts stay sequential and index locality is preserved. Generated
in Python via `uuid-utils`, not by the database, so the identifier exists before the insert
— which is what makes offline-created records work (ADR-0016). The client generates the
v7 and it becomes the server identifier, so sync retries are idempotent by construction.

Postgres 18 has a native `uuidv7()`; we do not use it, for the reason above.

**Public capability tokens: not UUIDs.** 16 bytes from `secrets.token_urlsafe`,
approximately 22 characters. Opaque, revocable, carrying no timestamp. A v7 token would
leak its creation time, and 36 characters produces a denser, less reliable QR.

**Short links: a separately encoded identifier**, base62 over a sequence, e.g.
`example.net/c/a7Kd9x`. Optimised purely for length.

## Consequences

**Good.** Healthy index locality on the tables that only grow.

**Good.** Client-generated IDs make offline sync idempotent without a separate
reconciliation key.

**Good.** Public tokens leak nothing and can be rotated without touching the underlying
record.

**Bad.** Three identifier concepts is more to explain than one. Mitigated by naming: `id`
is always internal, `token` is always public, `slug` is always human-chosen.

**Bad.** UUIDv7 does leak creation time to anyone who obtains the internal ID. Acceptable
because internal IDs are never exposed on public surfaces — enforced by API serialization
rules in `contracts/api-conventions.md`.

**Bad.** `uuid-utils` is a dependency with native code. Pin it and verify wheels exist for
the deployment platform.

## Alternatives considered

**UUIDv4 everywhere.** Rejected on index locality.

**Serial integers.** Rejected: enumerable, leaks volume, and breaks offline generation.

**ULID.** Functionally equivalent to v7 with worse ecosystem support in Postgres. v7 is
the standard.
