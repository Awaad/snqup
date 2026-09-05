# ADR-0012: Aggregate-only organizer analytics

**Status:** Accepted
**Date:** 2026-09-04

## Context

The original specification proposed a connection graph for organizers, showing who
connected with whom.

Relationship data is personal data about both parties. Disclosing to an organizer that
Sarah connected with Marcus is a third-party disclosure that neither Sarah nor Marcus
consented to when they exchanged cards with each other. There is no lawful basis for it
under GDPR without explicit, separate, per-party consent — and a graph built only from
doubly-consenting pairs would be so sparse as to be useless.

Attendee list export raises the same issue in a different form. "Downloadable attendee list
(only with consent)" is not a specification: consent from whom, in what wording, stored
where, revocable how, and what happens to a CSV already downloaded.

## Decision

**Organizer analytics are aggregate only.** Specifically permitted:

- Connections over time (the live curve — the projector feature)
- Total connections, unique connectors
- Connections per attendee, as a distribution
- **Percentage of attendees who made at least one connection** — the number organizers
  are actually asked about
- Peak activity windows, which tells them whether the coffee break worked
- Coarse cross-group mixing indicators, without identifying individuals
- Count of scans by non-users, so the fallback surface is measurable

**Explicitly forbidden:** any view, export or API response that reveals which specific
attendee connected with which specific attendee.

**Attendee list export requires a per-event consent record** carrying: attendee, event,
scope, timestamp, policy version, and the exact consent wording shown. Consent is
revocable, and revocation removes the attendee from future exports.

**We cannot claw back an already-downloaded CSV.** This is stated plainly in the consent
wording shown to attendees and in the organizer terms. It is a documented limitation, not
a gap.

**Leaderboards are off by default,** organizer-enabled, with per-attendee opt-out.
Gamifying scan counts produces farmed, worthless connections.

Time zones: events store UTC plus an IANA timezone string. "Peak activity" is meaningless
without it, and an offset is not sufficient because of DST.

## Consequences

**Good.** Defensible GDPR position with no per-pair consent machinery.

**Good.** The metrics that survive are the ones organizers actually need. "78% of your
attendees made at least one connection" is a better sell than a graph nobody can read.

**Good.** Removes an entire category of privacy incident.

**Bad.** The connection graph was a differentiator on the pitch deck. It is gone.

**Bad.** Aggregate metrics on small events can still identify individuals. An event with
three attendees where "2 of 3 connected" is disclosive. Suppress all aggregate metrics
below a minimum cohort size of 10.

**Bad.** Consent records add a table and a UI surface for something users will click
through without reading.

## Alternatives considered

**Graph with per-party opt-in.** Rejected: sparse to the point of uselessness, and it
implies we would build the machinery anyway.

**Anonymised graph (nodes without names).** Rejected: trivially re-identifiable at an
event where the organizer holds the registration list.
