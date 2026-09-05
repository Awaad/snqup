# ADR-0020: Data lifecycle: soft delete, purge job, dual export paths

**Status:** Accepted
**Date:** 2026-09-04

## Context

The product holds professional contact data about people who are not necessarily our
users. Card snapshots (ADR-0004) replicate personal data into records the subject does not
control. Connection views (ADR-0003) mean deletion is asymmetric by design.

Apple guideline 5.1.1(v) requires in-app account deletion. GDPR requires erasure and
portability. Both must actually work, not merely appear to.

There is also a product requirement: the entire value of the app is the user's contacts,
so losing them is catastrophic and generates the worst reviews.

## Decision

**Soft delete everywhere.** `deleted_at timestamptz NULL` on every user-facing table.
Application queries filter it via the repository layer.

**Soft delete alone does not satisfy erasure.** A scheduled purge job hard-deletes soft-
deleted rows after 30 days. Without the purge, `deleted_at` is a lie told to a regulator.

**Account deletion:**

1. In-app, no support contact required, no dark patterns.
2. Immediate: account disabled, all tokens revoked, public pages 404, cards unresolvable.
3. 30-day grace period during which the user can restore, clearly communicated.
4. After 30 days: the purge deletes the `user_profiles` row, plus cards, tokens, the
   erased user's own connection views and their analytics. The `users` anchor survives.

**Why personal data lives in a separate table.** A schema review executed against
PostgreSQL 16 found that deleting the `users` row cascaded through `connections` and
destroyed the counterparty's record of the meeting *and their private notes about it*,
directly contradicting the retention position below.

`users` now holds **no personal data at all** — an id and timestamps. Everything personal
is in `user_profiles`, and erasure is a single `DELETE` of that row. The foreign keys
from `connections` point at the anchor and are `ON DELETE RESTRICT`, so it cannot be
removed while a connection references it.

The alternative — anonymising the `users` row column by column — also worked, and was
rejected because it depends on a purge job that nulls every personal column. Adding a
column later and forgetting the purge list would be a silent compliance defect with no
test that catches it. **The rule the split encodes: if a column is personal data, it
belongs in `user_profiles`.** New personal columns are then covered by construction.

**What survives erasure, and why.** A card snapshot held by another user in their
connection history is **retained**. Our position: a snapshot is equivalent to a paper
business card that was voluntarily handed over, and the recipient retains it. The
recipient's connection history is their own record of a meeting that happened.

This position is documented in the privacy policy, disclosed at exchange time, and is a
decision rather than an accident. It may be challenged. If it is, the fallback is to
tombstone the snapshot to name only.

Live card links break on erasure. Only the snapshot survives, and only in the counterpart's
private view.

**Two distinct export paths that must not be confused:**

| | GDPR export | Workflow export |
|---|---|---|
| Legal basis | Article 20 obligation | Product feature |
| Price | **Always free** | Paid tier |
| Scope | Complete account data | Filtered selections |
| Format | JSON, machine-readable | vCard / CSV |
| Location | Settings → Privacy | Connections screen |
| Limits | None | Rate limited |

They have different names, different entry points and different copy. Gating the paid
export must never read as gating a legal right.

**Recovery.** Cloud sync is the default and is not optional for account holders. Recovery
from a lost phone must work from a fresh install with only an email address. This is
tested as part of the release checklist.

**Multi-device:** each device holds a local store; the cloud is canonical. Conflict rules
are in ADR-0016.

## Consequences

**Good.** Erasure is real, and demonstrably so.

**Good.** The 30-day grace period turns accidental deletion from catastrophe into a
support conversation.

**Good.** Free GDPR export is also a trust argument that helps conversion for a product
asking to hold someone's professional network.

**Bad.** The snapshot retention position is defensible but contestable. It must be
disclosed prominently, and we should expect to have to argue it at least once.

**Bad.** The purge job is critical and silent. If it fails for a month nobody notices
until an audit. It must alert on failure, not merely log.

**Bad.** Soft delete means every query needs the filter. The repository chokepoint
(ADR-0018) is what makes this safe.

**Bad.** Two export paths is duplicated engineering for what users perceive as one
feature.

## Alternatives considered

**Hard delete immediately.** Rejected: no recovery from accidental deletion, and it
destroys the counterpart's legitimate history.

**Erasing snapshots on subject request.** Rejected as default: it silently rewrites other
users' meeting records. Retained as the fallback position if challenged.

**Anonymising rather than deleting.** Rejected: contact data does not anonymise
meaningfully. A name and a company are the data.
