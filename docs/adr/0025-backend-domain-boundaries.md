# ADR-0025: Backend domain boundaries with enforced import rules

**Status:** Accepted
**Date:** 2026-09-04

## Context

`handoff/03-backend-api.md` described a layer-first structure (`routers/`, `services/`,
`repositories/`). That works at small scale and degrades predictably: as the number of
entities grows, related code spreads across four directories and every change touches all
of them.

The specific failure mode we are guarding against is coupling by convenience. A developer
in the events domain needs a card field, imports the cards repository directly, and now
events depends on the cards table layout. Six of those and the domains are notional.

Convention alone does not hold this line. It has to be enforced by a tool.

## Decision

**Domain-first layout.** Each domain owns its router, service, repository, models and
schemas.

```
apps/api/src/acme/
  domains/
    identity/     users, organizations, members, domain verification
    cards/        cards, tokens, themes, QR generation
    exchange/     the scan → connection transaction (orchestrates others)
    connections/  views, notes, tags, reminders, merge
    events/       events, attendees, staff, roster, dashboard aggregates
    billing/      entitlements, webhooks, reconciliation
    safety/       reports, blocks, audit log
    notifications/ device tokens, notification inbox
  core/          db, cache, auth adapter, errors, logging, jobs runtime
```

**`notifications` was added while writing the models**, and is not in the original list.
Device tokens and the notification inbox are used by `connections` (follow-up reminders,
reciprocity nudges), `events` (announcements, post-event digest) and `identity` (account
mail). Putting them inside any one of those would force the other two to import across a
boundary for something none of them owns.

**The import rule:**

- A domain may import another domain's **`service` module only**.
- A domain may **never** import another domain's repository, ORM models, or internal
  helpers.
- Any domain may import `core/`.
- `core/` imports no domain.

**Enforced by `import-linter` in CI**, not by review. Without the tool this decays within a
month. It has already caught two violations that passed type-checking and review: a
router importing `acme.api`, and `cards.service` reaching into `identity.models` for the
reserved-slug check.

`allow_indirect_imports` is **true**. The rule is "do not import another domain's
internals *directly*". A domain's own service necessarily uses its own repository, so
forbidding the transitive path would report `cards.service -> identity.service ->
identity.repository` as a violation and leave no legal way to call across domains at
all.

**`exchange` is its own domain, not part of `connections`.** It touches cards,
connections, events and safety in one transaction. It is the most important code in the
product and it should be findable in one place rather than hidden inside a larger domain.

**`billing` exposes exactly one thing to other domains:** `entitlements.check(subject,
key)`. Every domain reads it; none of them may reach past it. That narrow read-only
interface is what makes the ubiquitous dependency acceptable.

**Cross-domain writes** go through a service call or an internal event, never a direct
table write. A domain owns its tables.

## Consequences

**Good.** A feature change is contained in one directory. Onboarding is "read one domain"
rather than "read four layers".

**Good.** Coupling becomes a build failure rather than a code review argument.

**Good.** The domains map one-to-one onto the mobile and web feature directories
(ADR-0026), so the same mental model works across three codebases.

**Bad.** Some genuinely shared logic has no obvious home and will be duplicated or forced
into `core/`. Accept a small amount of duplication rather than a `shared/` dumping
ground, which becomes a hidden coupling point.

**Bad.** `exchange` orchestrating four domains makes it the most connected module in the
system. Its tests are correspondingly the most important (`handoff/00-shared-contracts.md`).

**Bad.** Going through service interfaces instead of joining tables costs some query
efficiency. Where a cross-domain read is genuinely hot, the answer is a purpose-built read
model in the calling domain, not a boundary violation.

**Bad.** `import-linter` is another CI step and another config file to maintain.

**Note on model registration.** `registry.py` imports every domain's `models`
module so `Base.metadata` is complete for Alembic and the drift test. It is the one
module permitted to import across domain boundaries, it imports only models, and nothing
imports it except `env.py` and the drift test.

**Note on foreign keys.** Cross-domain FKs are declared by table name string
(`ForeignKey("users.id")`), never by importing the other domain's model class. A string
carries no import. Consequently **no ORM `relationship()` crosses a domain boundary** —
cross-domain data comes from a service call. That costs some convenience and is the
entire reason the boundary holds.

**Note.** The shared infrastructure package is named `core/`, not `platform/`. `platform`
is a Python standard library module name; absolute imports resolve correctly, but the
shadowing confuses readers and some tooling.

## Alternatives considered

**Layer-first, as originally handed off.** Rejected: related code spreads, and there is no
natural boundary to enforce.

**Full hexagonal architecture with ports and adapters throughout.** Rejected: the ceremony
pays off with multiple teams and multiple external integrations. At this size it costs
velocity for structure we do not yet need.

**Separate services per domain.** Rejected outright. A distributed system for a product
with no users is the most expensive mistake available here.
