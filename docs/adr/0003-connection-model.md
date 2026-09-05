# ADR-0003: Connection model: immutable edge plus per-user view

**Status:** Accepted
**Date:** 2026-09-04

## Context

The original specification stored one symmetric row per connection, carrying `note`,
`tags` and `reminder_date` directly on it.

Two defects follow from that shape.

**Privacy.** Both users write private notes to the same row. Any query or serialization
bug exposes user A's private note about user B *to* user B. Notes will contain things like
"seemed unprepared, low priority". This is the single worst data leak available in this
product.

**Deletion.** There is no way for A to delete a connection while B keeps theirs. Both
users will expect this, and GDPR erasure requires it.

Separately, the proposed unique constraint `(user_a_id, user_b_id, event_id)` does not
work. Postgres treats NULLs as distinct in unique indexes, so every non-event connection
would permit unlimited duplicates. The specification's suggested workaround — a
placeholder event ID — breaks referential integrity against the `events` table.

## Decision

Split into two tables.

**`connections`** is the immutable edge. Participants, timestamp, event, both card
identifiers, both card snapshots, scan channel. Written once at exchange. Never carries
per-user private data.

**`connection_views`** is one row per `(user_id, connection_id)`. Notes, tags, reminders,
`archived_at`, `deleted_at`, merge pointer. Each user owns their own row and can never
read the other's.

Deletion is per-view. The edge survives until both views are deleted, then the purge job
removes it.

For uniqueness, use **two partial unique indexes** rather than one constraint:

```sql
CREATE UNIQUE INDEX ... ON connections (user_low_id, user_high_id, event_id)
  WHERE event_id IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX ... ON connections (user_low_id, user_high_id)
  WHERE event_id IS NULL AND deleted_at IS NULL;
```

Canonical ordering is enforced by a check constraint requiring `user_low_id < user_high_id`,
which prevents the same pair being stored twice in opposite order.

## Consequences

**Good.** Private notes are structurally unreachable by the other party. Not
"protected by a WHERE clause" — in a different row with a different owner.

**Good.** Asymmetric deletion works naturally, which GDPR requires.

**Good.** Duplicate prevention actually functions for both event and non-event
connections.

**Bad.** Every connection read is a join. Mitigated by indexing on
`connection_views(user_id, created_at DESC)`, which is the access pattern for the list
screen anyway.

**Bad.** Two writes per exchange instead of one. Must be in a single transaction.

**Bad.** "Delete" is ambiguous in the UI and needs careful copy: the user is removing
their copy, not erasing the meeting from history.

## Alternatives considered

**Two directed rows (A→B and B→A).** Rejected: doubles storage, and keeping shared facts
like the event tag consistent across two rows invites drift.

**Single row with `note_a` and `note_b` columns.** Rejected: the leak risk is identical,
merely renamed. Authorization would depend on knowing which side you are, in every query.
