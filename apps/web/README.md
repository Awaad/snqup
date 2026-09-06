# @acme/web

Authenticated application: organizer dashboard, event management, organization
settings, **and all organizer billing**.

Serves `app.example.com`. Not indexed, no UGC (ADR-0008).

## What lives here and nowhere else

**Organizer and organization billing.** Sold on the web only, through Stripe. The mobile
app contains no organizer upsell, no link and no mention of organizer pricing. That is an
App Store compliance requirement, not a preference (ADR-0009), and CI has a guard for it.

## Architecture (ADR-0026)

Server Components for fetching. Client Components only where interaction demands it.
TanStack Query on the client only for genuinely interactive surfaces such as dashboard
filters.

`src/core/sse/` wraps `EventSource` with reconnect and `Last-Event-ID` replay. It is the
demo-critical feature and must not be scattered across components.

## Dashboard rules

**Aggregates only** (ADR-0012). No view, export or API response may reveal which attendee
connected with which. Suppress every aggregate below a cohort of 10, because "2 of 3
connected" identifies people.

The live count ticking up on a projector at the venue is the feature. It has to be
reliable at a bad venue on bad wifi, in front of a paying customer.
