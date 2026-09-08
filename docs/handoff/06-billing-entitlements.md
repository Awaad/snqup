# Handoff: Billing and Entitlements

**Status: BUILT except OAuth credentials.** Three webhook sources (Apple, Stripe, Google Play), entitlements resolver, reconciliation job. Google Play is no longer deferred — see the ADR-0009 amendment.

**Owns:** the entitlements resolver, Apple and Stripe webhook handlers, reconciliation,
StoreKit integration on mobile, Stripe checkout on web.

**Cross-cutting.** Touches Backend, Mobile and Web. Read ADR-0009 before writing any code.

## The one rule

**No code anywhere asks about a payment provider.**

```python
# Correct
if not entitlements.check(subject, "connection.export"):
    raise ApiError("CONNECTION_EXPORT_NOT_ENTITLED")

# Wrong, and will be rejected in review
if not user.stripe_subscription_active:
    ...
```

Adding Google Play later must be one webhook handler and zero changes elsewhere. That is
the entire point of the design.

## Placement rules (compliance, not preference)

| Product | Where sold | Provider |
|---|---|---|
| Consumer Pro | In-app | **StoreKit** (Apple requires it) |
| Consumer Pro | Website | Stripe (12% better margin) |
| Organizer plans | Website **only** | Stripe |
| Organization plans | Website **only** | Stripe |

**The mobile app contains no organizer upsell.** No button, no link, no mention. It will be
tempting on the events screen. It is an App Store rejection risk.

External purchase links (post-Epic, US-only, under appeal) sit behind a per-region feature
flag, default off. Do not architect as though the ruling survives.

## Never store a price

Prices live in App Store Connect and Stripe. The database holds plan definitions and
entitlement mappings.

- StoreKit returns localised prices automatically. Use them.
- Stripe Price objects are read at render time.
- **No string like "$4.99" anywhere in the codebase**, including marketing copy — that goes
  through a CMS-less MDX file that reads from Stripe, or is manually maintained with a
  reminder.

Entitlement keys are in `00-context/pricing.md` and are stable even when prices change.

## Webhook handlers

**Idempotent against `billing_events`.** Apple notifications are eventually consistent and
occasionally duplicated. A handler that assumes exactly-once delivery will corrupt
entitlement state.

```
receive → verify signature → insert into billing_events (unique on source+external_id)
        → if already present, return 200 and stop
        → process → update subscriptions → recompute entitlements → mark processed
```

Signature verification failure returns `BILLING_WEBHOOK_INVALID`. Never process an
unverified payload.

## Reconciliation

Webhooks get missed. A periodic job compares our `subscriptions` against Apple's and
Stripe's records and repairs divergence.

**This job must alert on divergence**, not silently repair, because repeated divergence
indicates a handler bug.

## Conflict resolution

A user can hold both an Apple and a Stripe subscription — usually by subscribing on the
web, forgetting, and subscribing again in the app.

- Resolve deterministically: **highest entitlement wins**, both recorded.
- Surface it in support tooling.
- `runbooks/` needs a documented procedure for refunding the duplicate, since the refund
  path differs by provider (Apple refunds go through Apple, not us).

## Manual grants

Comped pilot partners are a manual grant with an expiry, using the same table and the same
resolver. **No special-case code.** `source = 'manual'`, `expires_at` set.

This matters: an organizer plan comped indefinitely is how the entire meetup segment ends
up never paying.

## Ticketing is out of scope

Selling tickets makes us a marketplace: Stripe Connect, per-organizer KYC, payout
schedules, refunds, chargeback liability, and tax obligations in every jurisdiction sold
into. That is a business, not a feature.

The schema is shaped so a `ticket_types` table can be added without touching the core. Do
not start it.

## Definition of done

- [ ] Entitlements resolver: every source, conflict, expiry and grace combination tested
- [ ] Both webhook handlers idempotent, verified by replaying a duplicate delivery
- [ ] Signature verification tested with an invalid signature
- [ ] Reconciliation job runs and alerts on divergence
- [ ] Apple sandbox purchase → entitlement granted, end to end
- [ ] Stripe test mode purchase → entitlement granted, end to end
- [ ] Subscription expiry → entitlement revoked, verified
- [ ] Refund → entitlement revoked, verified
- [ ] No price string anywhere in the codebase (grep it)
- [ ] No organizer upsell reachable from the mobile app (grep the bundle)
