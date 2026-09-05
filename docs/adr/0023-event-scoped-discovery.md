# ADR-0023: Event-scoped discovery, deferred but schema-ready

**Status:** Accepted
**Date:** 2026-09-04

## Context

A proposal was raised for a "be public" feature: users appear to others as a person of
interest based on shared field, co-attendance at events, or mutual connections.

The idea has real merit. The actual problem at a conference is not exchanging cards — it
is not knowing who to talk to. But the proposal splits into two very different features
with very different risk profiles.

## Decision

**Build (in v1.1): event-scoped, mutual, opt-in discovery.**

During an event, a user opts in to being discoverable. They see other opted-in attendees
with a stated reason: same industry, shared event history, mutual connections. Both sides
must have opted in. Discoverability expires when the event ends.

**Do not build: a persistent public directory.** Always-on discoverability, browsable
outside events, is LinkedIn — and LinkedIn wins because it has a billion profiles. A sparse
directory is worse than no directory. It also converts the product into a social network
with the attendant moderation, harassment and stalking-risk surface, and it invites the
"why not just use LinkedIn" comparison we are trying to avoid.

**Mutual-connection-based suggestions outside events are cut entirely.** Telling a stranger
they share three connections with you discloses something about both parties that neither
volunteered. Inside an event with mutual opt-in it is defensible. Outside it is not.

**Required alongside the feature:** block and report, opt-out mid-event with immediate
effect, no messaging before connection, no precise location, and organizer ability to
remove a participant.

**Schema-ready in v1.** These columns ship unused so v1.1 requires no migration:

- `event_attendees.discoverable_at timestamptz NULL`
- `event_attendees.discovery_prefs jsonb`
- `connections.visibility` enum — required because computing mutual-connection counts needs
  to know which edges are eligible

`pgvector` is enabled but unused, since industry-similarity matching will likely want
embeddings and enabling an extension later is trivial but easy to forget.

## Consequences

**Good.** Event scoping eliminates cold start. A 300-person event needs perhaps 60 opted-in
people to feel alive; a global directory needs a hundred thousand.

**Good.** Safety improves structurally: time-boxed, context-bound, and everyone present
paid to be in the same room. Persistent discovery would let someone browse for targets
indefinitely.

**Good.** Legally clean — explicit opt-in, obvious purpose, automatic expiry. Textbook data
minimisation.

**Good.** Strengthens the organizer pitch considerably. "Our attendees found the right
people" sells better than "our attendees exchanged 400 cards."

**Bad.** Deferring means v1 ships without a feature that could differentiate it.

**Bad.** Unused columns in v1 will confuse anyone reading the schema without this ADR.
They are commented in `schema/schema.sql` pointing here.

**Bad.** Even scoped, this is the highest-risk feature in the product for harassment. It
needs its own safety review before it ships, not merely a code review.

## Alternatives considered

**Persistent public directory.** Rejected above.

**Organizer-curated matchmaking (organizer suggests introductions).** Interesting, and it
avoids the consent problem by routing through a party both attendees have a relationship
with. Worth revisiting; not scoped here.
