# Architecture Decision Records

## Format

Every ADR uses the same headings: Status, Context, Decision, Consequences, Alternatives
considered. Short is better than complete. An ADR that nobody reads has failed.

## Rules

- **Immutable once accepted.** To change a decision, write a new ADR and mark the old one
  `Superseded by ADR-XXXX`. Never edit an accepted ADR's Decision section.
- **Numbered sequentially**, never reused, never renumbered.
- **One decision per record.** If you find yourself writing "and also", split it.
- **Consequences must include the bad ones.** An ADR listing only benefits is marketing,
  not engineering. The section exists so that in eighteen months we know what we accepted.

## Index

| # | Title | Status |
|---|---|---|
| 0001 | Record architecture decisions | Accepted |
| 0002 | Two-tier QR: live tokens and static tokens | Accepted |
| 0003 | Connection model: immutable edge plus per-user view | Accepted |
| 0004 | Card snapshots at exchange time | Accepted |
| 0005 | Supabase for Postgres, Auth and Storage; FastAPI owns authorization | Accepted |
| 0006 | SSE over Postgres LISTEN/NOTIFY for realtime, not WebSockets | Accepted |
| 0007 | Valkey scope: rate limiting, idempotency, cache, queue | Accepted |
| 0008 | Three-domain separation for UGC isolation | Accepted |
| 0009 | Dual billing sources, one entitlements model | Accepted |
| 0010 | UUIDv7 internally, opaque tokens publicly | Accepted |
| 0011 | i18n and RTL from day one; backend returns error codes | Accepted |
| 0012 | Aggregate-only organizer analytics | Accepted |
| 0013 | Secrets: SOPS with age | Accepted |
| 0014 | Monorepo with generated cross-language contracts | Accepted |
| 0015 | Deploy topology: Hetzner, Vercel, Supabase | Accepted |
| 0016 | Offline-first sync with client-generated identifiers | Accepted |
| 0017 | Design tokens generated from a single source | Accepted |
| 0018 | Two tenant types: organizations and events | Accepted |
| 0019 | No CMS; constrained editor writing to our own database | Accepted |
| 0020 | Data lifecycle: soft delete, purge job, dual export paths | Accepted |
| 0021 | Runtime versions and upgrade policy | Accepted |
| 0022 | Single-release strategy with feature flags | Accepted |
| 0023 | Event-scoped discovery, deferred but schema-ready | Accepted |
| 0024 | Observability: Sentry, PostHog, structured logs, no OTel yet | Accepted |
| 0025 | Backend domain boundaries with enforced import rules | Accepted |
| 0026 | Client architecture: feature-first, TanStack, separate sync | Accepted |
