# ADR-0009: Dual billing sources, one entitlements model

**Status:** Accepted, amended 2026-09-07
**Date:** 2026-09-04

## Context

Apple requires in-app purchase for digital subscriptions sold and consumed inside an iOS
app. The consumer Pro tier is therefore StoreKit, at 15% under the Small Business Program.
The same tier sold on our website can go through Stripe at approximately 2.9% + $0.30.

Organizer and organization plans are business software sold on a website to a different
buyer, comparable to how Slack and Dropbox sell business plans. These go through Stripe.

In April 2025, following the Epic ruling, Apple was ordered to permit US apps to link out
to external purchase without commission and without the prior anti-steering restrictions.
This is **US-only** and under appeal. The EU has a separate regime under the DMA. We
should not architect around that ruling surviving.

The failure mode being avoided: writing authorization code that asks "does this user have
a Stripe subscription", then rewriting the entitlement layer when App Store review forces
StoreKit.

## Decision

**Multiple billing sources feed a single internal entitlements model.**

```
Apple App Store Server Notifications ─┐
Stripe webhooks ──────────────────────┼──▶ entitlements ──▶ API authorization
Google Play RTDN ─────────────────────┘
Manual grants (comped pilot partners) ┘
```

**AMENDED: Google Play is not future work.** This ADR originally deferred it, which was
wrong. Android ships at launch (`00-context/product-scope.md`), so Play billing ships at
launch — deferring RTDN means an Android subscriber pays and receives no entitlements at
all.

The three providers authenticate in three completely different ways, and that is the part
worth knowing before touching any of them:

| | Authentication |
|---|---|
| Stripe | HMAC over `timestamp.body` |
| Apple | signed JWS payload, chain rooted in Apple's CA |
| **Google Play** | **no body signature at all** — a Pub/Sub OIDC bearer token is the only thing authenticating the request |

An RTDN endpoint that skips OIDC verification is a public "grant me a subscription" API
that behaves identically to a working one. RTDN also carries no event id of its own and
Pub/Sub is at-least-once by design, so a dedup key is derived from the fields identifying
the state change.

The `entitlements` table holds: subject (user or organization), entitlement key, value,
source, source reference, status, expiry.

**No code anywhere asks about a payment provider.** All authorization goes through
`entitlements.check(subject, key)`. Adding Google Play later is one webhook handler and
zero changes elsewhere.

**Placement rules, which are compliance requirements and not preferences:**

- Consumer Pro is purchasable in-app via StoreKit, and on the web via Stripe.
- Organizer and organization plans are **web only**. The mobile app contains no organizer
  upsell, no "upgrade this event" button, and no link to organizer pricing.
- External purchase links may be enabled per-region behind a feature flag if the legal
  position permits. Default off.

**Prices are never stored in our database.** Plan definitions and entitlement mappings are
stored; prices are read from StoreKit (localised automatically) or Stripe Price objects at
render time.

**Ticketing is out of scope** (see `00-context/product-scope.md`). Selling tickets makes
us a marketplace: Stripe Connect, KYC per organizer, payout schedules, chargeback
liability, multi-jurisdiction tax. The events schema leaves room for a `ticket_types`
table without touching the core.

## Consequences

**Good.** App Store review cannot force an entitlement layer rewrite.

**Good.** Comped pilot partners are a manual grant with an expiry, using the same
mechanism as paid entitlements. No special-case code.

**Good.** Web checkout captures 12% more margin where users choose it.

**Bad.** Two webhook handlers, two reconciliation paths, two sets of failure modes. Apple
notifications in particular are eventually consistent and occasionally duplicated, so the
handler must be idempotent.

**Bad.** Subscription state can diverge between Apple's record and ours. Requires a
periodic reconciliation job, not just webhook handling.

**Bad.** A user could theoretically hold both an Apple and a Stripe subscription. The
entitlements layer must resolve conflicts deterministically (highest entitlement wins,
both recorded) and the support runbook must cover refunding the duplicate.

## Alternatives considered

**Stripe only, web-only purchase, app is sign-in only** (the Spotify/Netflix model).
Rejected for the consumer tier: it materially reduces conversion for an impulse-priced
product, and historically Apple did not permit even mentioning that a web option exists.
Adopted for organizer plans, where it is appropriate.

**RevenueCat as a billing abstraction.** Reasonable and worth revisiting. Rejected for now
because it is a recurring cost and a third party in the payment path, for an abstraction
we can express in one table.
