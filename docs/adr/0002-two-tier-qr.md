# ADR-0002: Two-tier QR: live tokens and static tokens

**Status:** Accepted
**Date:** 2026-09-04

## Context

The original specification called for a single QR code encoding a stable card identifier,
with two-way automatic exchange enabled by default.

This is a harvesting and spam vector. A QR image can be obtained without the owner's
knowledge or presence: a photograph of a conference badge, a screenshot, or the image
embedded in a public link-in-bio page. Under the original design, anyone holding that
image could both harvest the card and silently push their own card into the owner's
connection list.

Concretely: scrape 500 badge photographs from a conference hashtag, resolve all of them,
and you have 500 contacts while 500 people have unknowingly received yours. The product
becomes a cold outreach tool.

The underlying problem is that a static image cannot express contemporaneous consent.

## Decision

Split the QR into two distinct token types with different exchange semantics.

**Live token.** Rendered in-app only, never exported, never printed. TTL of 10–15 minutes.
Scanning it triggers the full symmetric exchange, because the owner had to actively open
the app and present it. That act *is* the consent.

**Static token.** Printable, exportable as an image, embedded in the link-in-bio page and
written to NFC tags. **One-way only.** The scanner receives the public card. The owner
receives a pending request and never an automatic exchange. Revocable and rotatable, so a
leaked badge photograph can be killed without reprinting anything.

Both types resolve through the same endpoint and the same token table, differing only by
type and TTL. NFC and Wallet passes carry static tokens.

## Consequences

**Good.** Closes the harvesting vector, closes the spam vector, and gives us a defensible
GDPR position: symmetric exchange only ever occurs with contemporaneous mutual presence.

**Good.** Token rotation gives users a real remedy when a code leaks.

**Good.** One resolution path for QR, NFC and Wallet means adding NFC in v1.1 costs one
enum value.

**Bad.** Two token types is more surface than one. Users may not understand why the code
on their badge behaves differently from the one on their screen. This is a UX writing
problem and it is real.

**Bad.** Live tokens require the app to be open and online-ish to mint. The offline path
(ADR-0016) needs a pre-minted token cached on device.

## Alternatives considered

**Single token with a per-scan consent prompt to the owner.** Rejected: a push
notification asking "did you just meet this person?" arrives after the moment has passed
and will be dismissed. It also does not stop harvesting, only the reciprocal spam half.

**Proximity verification (Bluetooth, ultrasound).** Rejected: unreliable across devices,
significant battery and permission cost, and it fails exactly where it is needed most,
in a crowded hall.

**Static-only, always one-way.** Rejected: loses the symmetric exchange that is the
product's core differentiator.
