# Handoff: Web (Next.js)

**Owns:** `apps/web/`, `apps/public/`, `apps/marketing/`.

**Depends on:** Backend (generated client), Design system (tokens).

## Three applications, three domains, different risk profiles

This split is the whole point of ADR-0008. Do not merge them for convenience.

| App | Domain | UGC | Indexed |
|---|---|---|---|
| `marketing` | `example.com` | **No** | Yes |
| `web` | `app.example.com` | No | No |
| `public` | `example.net` | **Yes** | Selectively |

**Public event pages are the exception** and live on the primary domain at
`example.com/events/...`, gated behind organizer verification. That gate is both the SEO
strategy and the anti-spam filter: nobody pays to host a phishing page.

## Why the separation exists

Any product where strangers create pages with their own name, photo and outbound links is
a phishing host. When enough get reported, Safe Browsing blocklists **the domain**, not
the path — and then every link in the product shows a red interstitial in Chrome, Safari,
Gmail, WhatsApp and Slack simultaneously.

Controls on `public`:
- `noindex` by default; indexing requires an account trust signal
- `rel="nofollow ugc"` on every outbound user link
- Link scanning on save
- Reachable `abuse@` and a takedown path (`runbooks/abuse-takedown.md`)

## `apps/public` is the highest-traffic surface

Two distinct pages, often conflated. They are not the same.

**Link-in-bio page** (`/u/<slug>`): genuinely public, guessable, indexable. Server-rendered
on request against live data with a short cache — **not statically generated**, because a
card edit must appear immediately.

**Scan-resolution page** (`/c/<token>`): **not guessable**. Opaque token, always `noindex`,
revocable — so a leaked badge photo is fixed by rotating the token, not reprinting.

### The scan-resolution page is the growth loop

Most scanners will not have the app. This page must be flawless:

- **vCard save** — one tap, contact in their phone. This is the primary action.
- **Wallet add**
- Optional reply form → creates a pending exchange, emails them the card, invites them to
  claim it. **Optional**: forcing it kills the save flow.
- Install prompt is **secondary**, never a wall.

Performance target: **under 1 second on hotel wifi**, on a mid-range phone. Aggressive
caching at Cloudflare, minimal JavaScript.

Anonymous scans are recorded (`anonymous_scans`) so organizers can report on them and we
can measure conversion.

## `apps/web`: organizer dashboard

**SSE, not WebSockets** (ADR-0006). Native `EventSource`, automatic reconnection,
`Last-Event-ID` for replay.

The live count ticking up on a projector at the venue is the feature. It has to be
reliable at a bad venue on bad wifi, in front of a paying customer.

**Aggregates only** (ADR-0012). No view or export reveals who connected with whom.
Suppress below a cohort of 10.

Also: attendee roster import, event CRUD with a Tiptap editor storing **sanitized JSON,
never raw HTML** (ADR-0019), export gated on `event.attendee_export` with consent records
checked.

**Organizer billing lives here and only here.** Stripe. The mobile app never mentions it.

## i18n and RTL

`next-intl`, locale-prefixed routes, `dir` on `<html>`.

**CSS logical properties only.** `margin-inline-start`, never `margin-left`.
Lint-enforced. Test in `ar`.

## Definition of done

- [ ] Scan-resolution page loads under 1s on throttled 3G, mid-range device
- [ ] vCard save works on iOS Safari, Android Chrome, and in-app browsers
      (Instagram, LinkedIn) — test these specifically, they break things
- [ ] Link page reflects a card edit immediately
- [ ] Token rotation kills the old URL
- [ ] SSE dashboard survives a network drop and reconnects with correct counts
- [ ] No aggregate shown for a cohort under 10
- [ ] Tiptap output sanitized on write **and** on read
- [ ] `noindex` verified on scan pages and default-off on link pages
- [ ] RTL verified in `ar`
- [ ] No organizer pricing reachable from anything the mobile app links to
