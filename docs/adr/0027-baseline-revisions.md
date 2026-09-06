# ADR-0027: Baseline revisions: constrained text, mandatory organizations, content split

**Status:** Accepted
**Date:** 2026-09-06
**Amends:** ADR-0018 (tenancy), ADR-0019 (no CMS)

## Context

The baseline migration had not been applied anywhere, so the schema was still free to
change at no cost. Four decisions were revisited before that window closed. Three of them
reverse earlier choices recorded in this tree.

The window matters: each of these is a one-file edit today and a data migration on a live
database later.

## Decision

### A. Postgres enums become `text` with a named `CHECK`

Enums were the wrong shape for a pre-launch system still discovering its domain.
`ALTER TYPE ... ADD VALUE` cannot run inside a transaction, and a value can never be
**removed** or safely renamed. Choosing a bad name today means carrying it forever or
running an add-migrate-remove dance across three releases.

A `CHECK` constraint is dropped and re-added in one ordinary transactional migration and
enforces exactly the same set. Storage costs a few bytes per row.

Constraints are named `<table>_<column>_check_values` so the drift test can locate them
and so a violation message names the column. The Python `StrEnum`s stay as the typed
source; only the database representation changed.

### B. `events.organization_id` is `NOT NULL`

Solo organizers get a **personal organization**, created at organizer signup and flagged
`organizations.is_personal`.

The nullable owner cost more than it saved. Every organization-scoped query needed a
second branch (`OR organization_id IS NULL AND created_by = ?`), which is a branch someone
eventually forgets — and forgetting it is either a cross-tenant leak or a silently empty
result. It also meant organizer billing attached to a user sometimes and an organization
other times, doubling the paths through the entitlements resolver.

**The personal organization gets an independent UUIDv7, never one derived from the user
id.** `entitlements.subject_id` is polymorphic with no foreign key (a deliberate
limitation, see `schema/review-2026-09-05.md`), so a shared identifier would make
`subject_id = X` ambiguous between user X and organization X — a silent billing bug in
the one column with nothing to catch it.

Organizations also gain `website`, `description` and `logo_path` as **columns**, not keys
in `brand`. The rule: anything queried or acted on gets a column; `brand` holds
presentation only. `website` in particular seeds `domain_verifications`, which gates
public indexed event pages. Without that rule `brand` becomes a junk drawer nobody can
query.

`cards.organization_id` stays nullable. A personal card genuinely has no organization;
an event always has an organizing entity.

### C. Presentational event content moves to `event_content`

`events` keeps operational data the product owns forever: join code, timezone, visibility,
limits. `event_content` holds what an organizer writes.

Content is read through `EventsService.content_for()`, never joined from a router. Storage
then becomes an implementation detail: structured session tables or an external CMS are a
resolver change, touching neither `events`, the dashboard, nor the API shape.

This does not contradict ADR-0019, which said don't add a CMS *now*. It makes that ADR's
"revisit later" cheap rather than aspirational.

It also keeps `events` narrow, which matters because the organizer dashboard queries it
constantly and should not drag JSONB blobs along.

### D. `connections.occurred_at`, distinct from `created_at`

When the meeting happened, supplied by the client, versus when the server learned about
it.

An offline exchange can sync hours later (ADR-0016). Keying any time-based metric on
`created_at` would attribute it to the moment the wifi came back — which would make the
peak-activity chart we sell to organizers (ADR-0012) spike at reconnection. That is worse
than no chart, because it looks plausible.

Client-supplied means device clocks, which drift and can be set deliberately, so the
service validates `occurred_at` against the event window before accepting it.

## Considered and rejected: `event_sessions`

Inferring which session a connection belongs to from its timestamp, so an organizer could
see "the AI panel generated 40 connections".

It works for a single-track conference. At an expo with parallel booths a timestamp maps
to dozens of candidate sessions, so it degrades to null exactly where events are largest
and organizers pay most. That is a narrow win for permanent schema.

The question it was meant to answer — "was this the crypto booth or the real estate
booth?" — is better answered by the counterparty's **company**, which is already on their
card. That is asserted by a person rather than derived from geometry, and it survives a
conversation that starts at a booth and finishes in the corridor.

The same data supports exhibitor attribution ("Crypto Capital's staff generated 340
connections") with no new schema, which also points at exhibitors as a paying segment
distinct from organizers.

Revisit only if attendees start checking into sessions for some other reason. Note the
risk if it returns: sessions are the thin end of the agenda wedge, and speakers and
schedules are the conference-website product we are not building.

## Consequences

**Good.** Adding or removing a permitted value is now an ordinary reversible migration.
Organization-scoped queries have one path. The CMS question is deferred without being
foreclosed. Time-based analytics are correct for offline exchanges.

**Good.** All four were free to make. After launch, each is a data migration.

**Bad.** A personal organization row per organizer, most of which will never gain a second
member. Small, and organizers are a low-volume population.

**Bad.** `CHECK` constraints are less discoverable than enum types: `\dT` lists enums,
whereas finding permitted values means reading a constraint definition. Mitigated by the
naming convention and by `tests/test_enum_sync.py`.

**Bad.** Content behind a service interface is an extra hop for something that is one
table today. That indirection is the whole point, and it will look like overhead until
the day it isn't.

**Bad.** `occurred_at` is client-supplied and therefore untrusted. Validation reduces but
does not eliminate garbage; a device with a badly wrong clock inside the event window
still writes a plausible-looking wrong value.

## Alternatives considered

**Keeping enums and documenting the `ADD VALUE` pattern.** This was the first
recommendation and it was wrong. It assumed values would change roughly annually, which
was an invention; the system is pre-launch and `scan_channel` and `entitlement_source`
are both visibly going to move.

**A personal organization for every user, not just organizers.** Rejected: a row per
consumer signup for a feature most never touch, and it muddies erasure.
