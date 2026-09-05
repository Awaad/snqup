# Product Scope (v1)

**Status:** Frozen for v1. Changes require a scope amendment signed off by the product owner.

## One-liner

A digital contact identity that works as a card, a link, and a QR code, plus the tooling
for event organizers to measure the networking that happens at their events.

## Release model

**Everything ships in one release.** This was a deliberate decision, made against the
recommendation to phase it. The consequences are recorded in ADR-0022 and must be
mitigated as follows:

- Every major surface ships behind a feature flag so the binary can be submitted to the
  app stores while individual surfaces are still being finished.
- An early throwaway build is submitted to Apple review well before feature completion,
  purely to discover objections to guideline 5.1.2 (third-party data sharing) while there
  is still time to respond.
- The organizer dashboard is explicitly a hypothesis. It is built before any organizer has
  been observed using it. Budget for a rewrite of its analytics surface post-launch.

## In scope for v1

### Identity

- Multiple context-aware cards per user (Personal / Business / Custom)
- Card fields: name, photo, headline, company, email, phone, website, socials
- Custom fields (paid)
- Card themes, and QR customisation (colour, logo, corner style — paid)
- Public link-in-bio page per card, with custom slugs on paid plans
- Apple Wallet and Google Wallet passes carrying the card QR

### Exchange

- **Live QR**: rendered in-app only, short-lived token, triggers symmetric exchange
- **Static QR**: printable/exportable, one-way, produces a pending request
- NFC tags resolve to the same token URL as static QR (tag *writing* ships in v1.1;
  the resolution layer and the `scan_channel` field ship in v1)
- Offline scan with local queue and sync on reconnect
- Web fallback page for scanners with no app: vCard save, Wallet add, optional reply form
- Meeting context capture: a note (typed or voice) prompted immediately after exchange

### Connections

- Connection list with search and filter by event, card, date, tag
- Private per-user notes, tags, follow-up reminders
- Duplicate detection and merge
- Export as vCard / CSV (paid)
- CRM sync (paid, one integration at launch, more over time)
- Post-event digest: 24h after an event ends, a summary and follow-up prompt
- Reciprocity nudge: surfaces one-sided saves

### Events

- Event creation via web dashboard
- Event codes, three visibility levels (private / unlisted / public+indexed)
- Attendee list import from the organizer's registration data
- Live organizer dashboard over SSE
- Aggregate-only analytics (see ADR-0012 — no per-person connection graph)
- Push announcements to attendees
- Event staff roles separate from organization roles

### Organizations

- Team and company cards with enforced branding
- Seats, roles, domain verification
- Aggregate connection reporting for the organization

### Commercial

- Consumer subscription via StoreKit (in-app) and Stripe (web)
- Organizer and organization plans via Stripe, **web only, never mentioned in the app**
- Unified entitlements layer fed by both sources

### Compliance and safety

- Account deletion in-app
- Free GDPR data export, distinct from the paid workflow export
- Report and block
- Reserved slug list
- Domain-based verification badges
- Abuse takedown path on the UGC domain

## Explicitly out of scope for v1

Listed so that nobody builds them by accident.

| Item | Reason |
|---|---|
| Ticketing / payments to organizers | Turns us into a marketplace: Stripe Connect, KYC per organizer, payout schedules, chargeback liability, multi-jurisdiction tax. This is a business, not a feature. Schema leaves room (see `schema/schema.sql`). |
| In-app messaging | Moderation surface, notification burden, competes with channels users already have. Deep-link out instead. |
| AI follow-up drafting | Produces generic output people don't send. Revisit once we have real follow-up data. |
| Persistent public directory | Sparse directory is worse than none. We would lose to LinkedIn on its own ground. See ADR-0023. |
| Badge / ticketing platform integrations | Eventbrite, Cvent, Swapcard etc. are a partnership programme, not a sprint. |
| Phone/SMS authentication | Only expensive auth method, and the primary target for SMS pumping fraud. Email + Apple + Google covers everyone. |
| Android phone-to-phone NFC (HCE) | iOS does not expose the equivalent. An Android-only primary interaction is not viable. |
| Per-person connection graph for organizers | Third-party disclosure of relationship data neither party consented to. Aggregates only. |

## Deferred to v1.1, but schema-ready in v1

These must not require a migration when they land. The columns exist and go unused.

- **NFC tag writing** — needs `scan_channel` on connections (present in v1)
- **Event-scoped discovery** — needs `discoverable_at`, `discovery_prefs` on
  `event_attendees` and `visibility` on `connections` (all present in v1)
- **Physical card sales** — hardware line, likely higher margin than the subscription
- **Additional CRM integrations**

## Activation metric

The single number that tells us whether this works:

```
install → account created → first card created → first QR shown
       → FIRST SUCCESSFUL EXCHANGE → returned within 7 days
```

Step five is activation. Everything before it is setup, everything after is retention.
Instrument all six from the first build (see `handoff/08-qa-release.md`).

## Pilot success criteria

At the first live event pilot, the thresholds that indicate the category works:

- \>30% of attendees install
- \>50% of installs complete an exchange
- \>20% open the app again a week later without a push

Below 10% install with no return usage means the problem is the category, not the
execution, and no amount of engineering fixes it.

## Open product decisions with deadlines

| Decision | Blocks | Deadline |
|---|---|---|
| Final product name | Bundle IDs, package names, 2 domain purchases, App Store Connect | Before first store submission. **Blocking.** |
| Positioning lead (exchange / identity / events) | First screen after install, store listing copy, activation instrumentation weighting | Before store listing is written |
| Which CRM integration ships first | Backend integration work | Before backend sprint 3 |
