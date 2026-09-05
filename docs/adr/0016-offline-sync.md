# ADR-0016: Offline-first sync with client-generated identifiers

**Status:** Accepted
**Date:** 2026-09-04

## Context

Conference wifi is unreliable and some venues have no signal. An exchange that fails because
the network did is the worst possible failure: it happens in front of the other person, at
the exact moment the product is supposed to prove itself.

Separately, the same account on multiple devices, each with a local store, requires a
conflict rule. Naive last-write-wins at record level silently destroys notes when a user
edits on two devices.

## Decision

**The QR payload carries a compact preview.** Roughly 120–180 bytes: display name,
headline, company, card ID, token. That keeps the code around QR version 10–12, which
scans reliably at realistic distances.

The payload is **not signed.** A signature costs approximately 90 bytes and buys nothing,
because the server is the trust anchor from the moment sync occurs. Locally rendered
preview data is displayed marked as unverified, and the connection is marked pending until
the server confirms it.

**Client-generated UUIDv7 identifiers** (ADR-0010) mean an offline-created record already
has its final identifier. Sync is an idempotent upsert; retries are free and duplicate
submissions are naturally absorbed.

**Every mutating request carries a client-generated idempotency key**, held 24 hours in
Valkey (ADR-0007).

**Sync queue on device:** durable, ordered, with exponential backoff. Survives app restart
and force-quit. Visible to the user as a pending indicator, never hidden.

**Conflict resolution:**

- **Structured card fields:** last-write-wins at field level, using a per-field
  `updated_at`. Not record level — editing a phone number on one device must not revert a
  headline changed on another.
- **Connection notes:** **field-level merge with a conflict marker**, never silent
  overwrite. If both devices edited the note, both texts are preserved with a visible
  marker and the user resolves it. Notes are the highest-value user-authored data in the
  product and silently losing them is unacceptable.
- **Tags:** set union. Removals are tombstoned.
- **Server-authoritative, never merged:** entitlements, event membership, connection
  existence.

**Live tokens must be pre-minted and cached** on device so the offline user can still
present a valid code (ADR-0002).

## Consequences

**Good.** The exchange works in a basement, which is where exchanges happen.

**Good.** Idempotency by construction rather than by reconciliation.

**Good.** Notes are never silently lost.

**Bad.** The unsigned preview means a hand-crafted QR could display false information
before sync. Mitigated by the unverified marker and by the fact that the server corrects
it within seconds of reconnection. Accepted: the alternative is a denser, less reliable
code.

**Bad.** Conflict markers are visible user-facing complexity for a rare case. Accepted:
visible complexity beats invisible data loss.

**Bad.** Pre-minted cached live tokens weaken the TTL guarantee in ADR-0002, since a
cached token is valid for its full TTL from minting rather than from display. Bounded by
keeping the cache small and refreshing on every foreground.

**Bad.** A durable device queue is genuinely difficult to get right and needs dedicated
test coverage for restart, force-quit and partial-sync scenarios.

## Alternatives considered

**Online-only exchange.** Rejected: fails at the moment of truth.

**Signed offline payload.** Rejected: ~90 extra bytes pushes QR density past the reliable
threshold, for a guarantee the server provides seconds later anyway.

**CRDTs for note merging.** Correct and considerably more machinery than the case
warrants. Revisit if collaborative editing ever appears.
