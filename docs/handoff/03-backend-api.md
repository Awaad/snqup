# Handoff: Backend API

**Status: BUILT.** 32 endpoints, 227 tests, four import contracts. This document describes the design; the code is the authority where they differ.

**Owns:** `apps/api/`. FastAPI, Python 3.13 (ADR-0021).

**Depends on:** Platform (CI, staging), Database (schema).
**Blocks:** Mobile and Web, via the generated client.

## Read first

`00-shared-contracts.md`, `contracts/api-conventions.md`, `contracts/error-codes.md`,
`schema/schema.sql`, and ADRs 0002, 0003, 0005, 0006, 0007, 0009, 0016.

## Architecture

```
routers/      HTTP only. No business logic. Thin.
services/     Business logic. Where authorization lives.
repositories/ Data access. THE tenant chokepoint.
domain/       Pure models and rules. No I/O.
auth/         provider.py — the swappable IdP adapter
workers/      ARQ jobs
```

**The repository layer is the tenant chokepoint** (ADR-0018). Every tenant-scoped query
takes an explicit tenant ID parameter. Omitting the filter must be structurally
impossible, not merely discouraged. This is why we declined RLS (ADR-0005): authorization
lives in code that is reviewable and testable.

**`core/auth.py` is the only module that knows the IdP exists.** Swapping providers
touches this file and nothing else.

## The exchange endpoint

The most important code in the product. Exhaustive test coverage, no exceptions.

**Two token types with different semantics** (ADR-0002):

| | Live token | Static token |
|---|---|---|
| Where | In-app render only | Badge, export, link page, NFC, Wallet |
| TTL | 10–15 min | None; killed by `revoked_at` |
| Result | **Symmetric exchange** | **One-way.** Scanner gets the card; owner gets a pending request |

Never make a static token produce an automatic exchange. That is the harvesting vector the
whole design exists to close.

**Transaction, in one commit:**
1. Order the pair canonically (`user_low_id < user_high_id`)
2. Insert `connections` with **both card snapshots** (ADR-0004)
3. Insert two `connection_views`
4. `NOTIFY event_<id>` if event-scoped, for the SSE dashboard
5. Record `scan_channel` — required for attribution, and retrofitting loses history

**Paths that must be tested:** live, static, offline replay, duplicate, concurrent scan of
the same pair, blocked user, expired token, revoked token, self-scan, event-scoped,
non-event.

## Rate limiting (layered)

One global limit either blocks legitimate conference use or lets the harvester through.

| Layer | Limit | Note |
|---|---|---|
| Per static token | Tight | 200 resolutions/hour is scraping. Throttle → captcha → alert the owner |
| Per IP | Loose | A venue is hundreds of people behind one NAT. Ceiling only |
| Per authenticated scanner | Moderate | A human scans ~30/day, not 300 |
| Anonymous reply form | Tightest | Plus honeypot field and Turnstile |

**Scan limits fail open with logging** (ADR-0007). Sliding window in Valkey.

## SSE dashboard

`StreamingResponse`, Postgres `LISTEN/NOTIFY` for fan-out (ADR-0006). No Redis pub/sub,
no sticky sessions.

Reconnection replays via `Last-Event-ID`. Cheap, because the payload is a snapshot of
aggregate counts, not a delta stream.

**Aggregates only** (ADR-0012). No endpoint, view or export may reveal which attendee
connected with which. Suppress all aggregates below a cohort of 10.

**Deployment footgun:** the LISTEN connection must be outside the pooler. Coordinate with
Platform.

## Entitlements

`FREE_TIER` in `billing/service.py` is the free plan as code. A subject with no
entitlement rows resolves to it, which means a new user works without anything writing
rows at signup, and a billing outage degrades to the free tier rather than to "entitled
to nothing".

`entitlements_active_idx` is unique per *source*, so multiple rows for one key always
mean multiple sources — the Apple-plus-Stripe case. Conflicts resolve highest-wins, and
`-1` (unlimited) beats every finite value rather than losing to `max()`.



`entitlements.check(subject, key)` is the only authorization question about paid features.
**No code anywhere asks about a payment provider** (ADR-0009).

Webhook handlers (Apple, Stripe, Google) are **idempotent against `billing_events`**. Apple
notifications are eventually consistent and occasionally duplicated.

A periodic reconciliation job catches divergence that webhooks missed.

## Jobs (ARQ)

Every job must be **reconstructible from Postgres**. Valkey holds scheduling only
(ADR-0007). A job carrying state only in its queue payload is a bug.

- Follow-up reminders
- **Post-event digest**, 24h after `ends_at` in the event's local timezone: "You met 7
  people. 3 have no notes." This is the single best retention mechanic in the product.
- Reciprocity nudge
- Transactional email via Resend
- CSV/vCard export generation
- Purge job

## Definition of done

- [ ] Exchange endpoint: every path tested, including concurrency
- [ ] Entitlements resolver: every source/conflict/expiry combination tested
- [ ] Tenant isolation suite passes
- [ ] All errors use registered codes; no user-facing strings anywhere in the API
- [ ] Idempotency on every mutating endpoint
- [ ] `openapi.json` regenerates cleanly; client codegen passes in CI
- [ ] Redaction processor verified: no email, phone or token reachable in logs
- [ ] SSE tested against a real reconnect, not just a happy path
