# ADR-0004: Card snapshots at exchange time

**Status:** Accepted
**Date:** 2026-09-04

## Context

QR tokens remain valid across card edits, which is a deliberate feature: printed badges
must keep working after a job change.

It is also an impersonation vector. B presents a card reading "Engineer at Acme", exchanges
with 200 people, then rewrites it to "Recruiter at Competitor". Everyone who met B now
believes they received the second card. The record of what was actually exchanged is
destroyed by an edit.

There is a second, more mundane problem: "who did I meet at that conference in March" is
unanswerable if the answer mutates.

## Decision

At exchange time, write an immutable JSONB snapshot of both cards onto the `connections`
row: `card_low_snapshot`, `card_high_snapshot`. Each snapshot carries a
`schema_version` so old snapshots render correctly after the card schema evolves.

The UI shows the **live** card by default, with a subtle indicator when the live card
differs materially from the snapshot ("details changed since you met"). Tapping it reveals
the original.

If the source card is deleted, the snapshot remains and the live view degrades to "no
longer available".

## Consequences

**Good.** The historical record is truthful and the impersonation vector closes.

**Good.** Card deletion no longer cascades into destroying other people's connection
history, which is what makes ADR-0020's soft-delete story coherent.

**Good.** Offline and non-user scan paths have something to render without a live lookup.

**Bad.** Storage duplication. A few hundred bytes per connection. Irrelevant at any scale
we will reach.

**Bad.** Snapshots are personal data replicated outside the subject's direct control,
which must be disclosed in the privacy policy and handled in the erasure procedure
(ADR-0020). Our position: a snapshot is equivalent to a paper card that was handed over,
and the recipient retains it. This position must be documented, not assumed.

**Bad.** Card themes are versioned user data now, so the renderer must handle old theme
formats. See ADR-0017 for why card themes are kept separate from UI design tokens.

## Alternatives considered

**Live-only.** Rejected: impersonation vector stays open, history is not truthful.

**Snapshot-only.** Rejected: contacts go permanently stale, which defeats the point of a
digital card.

**Diff-based versioning of cards, with connections pointing at a version.** Rejected:
correct but significantly more machinery for no additional user-visible benefit.
