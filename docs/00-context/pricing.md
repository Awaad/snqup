# Pricing and Unit Economics

**Status:** Frozen. Engineering builds against the *entitlement keys* in this document,
never against the prices. Prices live in App Store Connect and Stripe (see ADR-0009).

## Principle

Infrastructure cost is approximately **$0.008 per monthly active user**. There is no cost
floor to defend. Pricing is therefore set entirely by willingness to pay.

The real costs are Apple's commission, Stripe's fees, and engineering time.

## Cost model at ~10,000 MAU

| Item | Monthly | Note |
|---|---|---|
| Supabase Pro | $25 | Covers 100k MAU auth, 8GB database |
| Hetzner CPX21 | ~€8 | FastAPI + Valkey |
| Vercel | $0–20 | Free tier until bandwidth grows |
| Object storage (photos) | ~$1 | ~200KB/user |
| Resend | $20 | Above the 3k/month free tier |
| Push (Expo) | $0 | |
| Sentry + PostHog | $0–26 | Free tiers stretch far |
| **Total** | **~$75–100** | |

Verify these against current published pricing before committing to a budget. They are
estimates.

## Consumer tiers

| | Free | Pro | Pro Annual |
|---|---|---|---|
| Price | $0 | **$4.99/mo** | **$39.99/yr** (=$3.33/mo) |
| Cards | 1 | 5 | 5 |
| **Connections** | **Unlimited, forever** | Unlimited | Unlimited |
| Notes and tags | Yes | Yes | Yes |
| Follow-up reminders | 3 active | Unlimited | Unlimited |
| Export (vCard/CSV) | **No** | Yes | Yes |
| CRM sync | No | Yes | Yes |
| Custom fields | No | Yes | Yes |
| Link-in-bio pages | 1 | 5, custom slugs | Same |
| Page and card analytics | No | Yes | Yes |
| Themes | 5 | All + custom colours | Same |
| QR customisation | No | Colour, logo, corners | Same |
| Remove branding | No | Yes | Yes |

**Net per subscriber:** ~$4.24/mo via StoreKit (15% Small Business Program),
~$4.55/mo via Stripe on web. Annual nets ~$34 through Apple.

### Reasoning, recorded so it is not re-litigated

- **$4.99 not $3.99.** Demand is inelastic at this price point and the psychological
  threshold is $5, not $4. 25% more revenue for no conversion cost.
- **Annual at a 33% discount** is aggressive on purpose. Annual subscribers churn far
  less, cash arrives upfront, and Apple's cut is 15% either way under SBP.
- **5 cards not 3.** Three reads as a limit, five reads as enough. Costs nothing.
- **Connection history is never gated.** Hiding contacts a user already made feels like
  theft and would generate more one-star reviews than every other issue combined.
- **Export is the sharpest paywall in the product.** Someone with 200 contacts who needs
  them in a CRM has an urgent, concrete job. That converts far better than card count,
  which is a limit people simply work around.
- **Reminders capped at 3, not 0.** The free user must experience the feature working
  before they will pay to uncap it.

## Organizer and organization tiers

Sold on the web only, through Stripe. **Never mentioned, linked, or upsold inside the
mobile app** (see ADR-0009 for why this is an App Store compliance requirement, not a
preference).

| | Starter | Pro | Business | Enterprise |
|---|---|---|---|---|
| Price | Free | **$49/event** or **$99/mo** | **$299/mo** | Custom |
| Attendees | 50 | 500 | 2,500 | Unlimited |
| Live dashboard | Basic counts | Full + SSE | Full | Full |
| **Attendee CSV export** | **No** | Yes | Yes | Yes |
| Attendee list import | No | Yes | Yes | Yes |
| Branded event page | No | Yes | Yes + custom domain | Yes |
| Push announcements | No | Yes | Yes | Yes |
| Events per month | 1 | Unlimited (monthly plan) | Unlimited | Unlimited |
| Team seats | 1 | 3 | 10 | Unlimited |
| Support | Community | Email | Priority | SLA + CSM |

### Reasoning

- **$29/event was too low.** Event budgets run to tens of thousands. Pricing at $29
  signals a toy. $299/mo is not expensive to a conference spending $40k on venue and
  catering.
- **Attendee bands** close the hole where a 5,000-person conference paid a flat $29.
- **CSV export is the organizer paywall.** The attendee list is the entire reason they run
  the event. Free proves the product works; export requires payment.
- **50 free attendees, not unlimited-free for launch partners.** Pilot partners get a
  comped Pro code with an expiry date. Same generosity, no permanent free tier eating the
  core segment.

## Where the revenue actually is

| Segment | Net ARPU | Volume | Character |
|---|---|---|---|
| Consumer Pro | ~$4/mo | High volume, 2–4% conversion | Funnel and data moat |
| Organizer Pro | ~$96/mo | Low volume, high conversion | **The business** |
| Organizer Business | ~$290/mo | Very low volume | Whale accounts |
| Organization / team | TBD | Very low volume, high value | Likely the largest tier |

Rough model at 10,000 MAU: 300 consumer subscribers ≈ $1,200/mo. Twenty organizer Pro
accounts ≈ $1,900/mo.

**Twenty organizers out-earn ten thousand consumers.** Engineering effort should be
weighted accordingly: the organizer dashboard matters more than card themes.

## Entitlement keys

These are the strings the codebase uses. They are stable and never change, even if
pricing does. Full definition lives in `handoff/06-billing-entitlements.md`.

```
card.limit                    int
card.custom_fields            bool
card.qr_customisation         bool
card.remove_branding          bool
link.page_limit               int
link.custom_slug              bool
analytics.card                bool
analytics.link                bool
connection.reminder_limit     int   (-1 = unlimited)
connection.export             bool
connection.crm_sync           bool
event.attendee_limit          int
event.dashboard_live          bool
event.attendee_export         bool
event.attendee_import         bool
event.branded_page            bool
event.custom_domain           bool
event.announcements           bool
org.seat_limit                int
```

**Never hardcode a price anywhere in the codebase.** Display prices are read from
StoreKit (localised automatically) or from Stripe Price objects at render time.
The database stores plan definitions and entitlements only.
