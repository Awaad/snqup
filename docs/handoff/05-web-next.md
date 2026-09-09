# Handoff: Web (Next.js)

**Owns:** four apps, three domains, one shared package.

**Status:** the API is **built and passing 304 tests**. Every endpoint below exists today
and `packages/api-client` is generated from its OpenAPI schema. Where this document and
the generated client disagree, the client is right.

**Read `04-mobile-expo.md` Part 1 first.** It is the product brief — who this is for, the
activation metric, and the three facts that shape every interface decision. It is not
mobile-specific and the rest of this document assumes it.

---

## The four apps and why they are separate

| App | Domain | What it is | Budget |
|---|---|---|---|
| `apps/public` | `example.net` | Card pages, scan resolution. **UGC** | **sub-1s on hotel wifi** |
| `apps/marketing` | `example.com` | Landing, pricing, SEO | normal |
| `apps/web` | `app.example.com` | Organizer dashboard, billing | normal |
| `apps/admin` | separate subdomain | Support console | normal |

**The domain split is a security boundary, not a preference (ADR-0008).**

User-generated cards live on `example.net` so that a phishing card getting the domain
blocklisted takes down UGC pages and **not** the marketing site, the dashboard, or the
password-reset emails. If they shared a domain, one abusive user could take down
everything, including the ability to tell customers what happened.

That has a consequence you must not work around: **no shared cookies between apps.** They
are separate origins on purpose. Do not add a wildcard cookie domain to make session
sharing convenient — it reunites what the split exists to separate.

---

## `apps/public` — the one that matters most

This is the highest-traffic surface in the product and the growth loop. **Most scanners do
not have the app**, so this page is the entire experience for the majority of people who
ever encounter us.

### The performance budget is a product requirement

Sub-one-second on hotel wifi, on a mid-range Android, cold. Not a target — a requirement.
Someone is standing in a hall waiting for it, having just pointed a camera at a stranger's
badge. A three-second load is a failed exchange.

That budget dictates the architecture:

- **Server-rendered.** No client-side data fetching for the card itself
- **Almost no JavaScript.** Ship the card as HTML. Interactivity is limited to the save
  and share buttons
- **No web fonts on the critical path.** System stack, or preloaded and swapped
- **Images through the CDN**, sized, `loading="eager"` for the avatar only
- **No analytics script that blocks render.** Beacon after paint or not at all

### Endpoints

```
POST /v1/scan/{token}                     → resolve a scanned token, records the scan
GET  /v1/cards/public/{slug}              → link-in-bio page
POST /v1/scan/{scan_id}/interactions      → record what they did
POST /v1/scan/{scan_id}/reply             → optional: leave their own details

GET  /v1/events/public/{slug}             → public event page
GET  /v1/events/code/{code}               → resolve a join code from a QR
POST /v1/events/public/{slug}/register    → register, NO account required
```

### The event page is the same growth loop as the scan page

An organizer puts a QR or "register at example.net/e/devcon" on a slide, a badge or a
printed programme. Someone scans it and is on the roster before installing anything.

- **`indexable: false` means render `noindex`.** Unlisted events resolve by link but must
  not enter a search index; public ones are the SEO strategy
- **The join code is returned on registration** so a visitor who *does* have the app can
  deep-link instead of typing it
- **The event page never exposes the code**, only the registration response does. A code
  is an invitation, and printing it on a public page would make every private event
  joinable by anyone who found the URL
- Registering twice is **not an error** — someone who taps twice, or who was already on
  the organizer's upload, sees success either way

All four are unauthenticated by design. `POST /v1/scan/{token}` returns
`{scan_id, card}` — hold the `scan_id`, everything else keys off it.

### Save and share are the conversion, and they are measurable

This is the part I would most like you to get right, and it came out of a good challenge
during the build.

The naive design shows contact details as text. Someone screenshots it. **Screenshots are
undetectable**, so that conversion is invisible to us and worse for the user.

Instead: **render every detail as a tappable control that fires an interaction.**

| Control | Fires | `target` |
|---|---|---|
| Save contact (vCard) | `vcard_save` | — |
| Add to Wallet | `wallet_add` | — |
| Phone number | `call` | `phone` |
| Email | `email` | `email` |
| Each social icon | `link_click` | `linkedin`, `x`, … |
| Each custom link | `link_click` | the label |
| Browser `copy` event | `copy` | the element copied |

```ts
await fetch(`/v1/scan/${scanId}/interactions`, {
  method: "POST",
  body: JSON.stringify({ kind: "link_click", target: "linkedin" }),
});
```

Idempotent per `(scan, kind, target)`, so a double-tap does not double-count and you can
fire optimistically without debouncing.

**Never send the copied content.** `target` names *which element* was copied, never what
was in it. That is the line between measuring engagement and reading over someone's
shoulder, and it is not negotiable.

**Say "at least" wherever these numbers surface.** Screenshots remain undetectable, so
every figure derived from this is a lower bound. Do not imply completeness.

### The reply form

Optional, and that word is load-bearing. Requiring details before the vCard download would
kill the save flow, which is the one thing this page must get right.

It is an unauthenticated write from a stranger, so it is honeypotted (`website` field —
leave it empty, a filled one returns success and does nothing) and rate-limited per IP.

### Security, because this is the UGC domain

- **Card content is Tiptap JSON, never raw HTML.** Sanitize on render as well as on write.
  User HTML here is the highest-consequence vulnerability in the product
- **Custom links are `https` only** — enforced server-side, but do not undo it client-side
- **`rel="nofollow ugc"` on every outbound link.** Without it we are a link farm and the
  domain's reputation is someone else's to spend
- **`noindex` on `/scan/{token}` pages.** Non-guessable tokens must not enter a search
  index. Slug pages at `/{slug}` **are** indexable — that is the SEO strategy

---

## `apps/web` — organizer dashboard

### Endpoints

```
POST /v1/organizations/{id}/events    create
POST /v1/events/{id}/roster           registration CSV
GET  /v1/events/{id}/stats            aggregates
GET  /v1/events/{id}/stream           SSE, live
```

Event creation and roster import are **web-only** — they are desk tasks with a form and a
file, deliberately absent from mobile.

**`POST /v1/events/join` is not here, but registration is** — on `apps/public`, not on the
dashboard. That distinction was a correction: an authenticated join flow would need
consumer auth on a domain that otherwise has none, but an organizer putting "register at
example.net/e/devcon" on a slide is a real surface and it belongs with the other
anonymous-first pages.

See the `apps/public` section for the event page and registration endpoints.

### The live dashboard

SSE, not WebSockets (ADR-0006). Use `EventSource`; it reconnects automatically.

```ts
const stream = new EventSource(`/v1/events/${eventId}/stream`);
stream.addEventListener("stats", (e) => setStats(JSON.parse(e.data)));
```

Three things to know:

- **The payload is a full snapshot, not a delta.** A dropped message is self-correcting.
  Do not accumulate; replace
- **Keepalive comments arrive every 25s.** `EventSource` ignores them. They exist because
  proxies close idle connections at 30–60s
- **This gets demoed on a projector at a venue.** It must survive bad wifi and look alive
  during a quiet stretch — hence the periodic snapshot even with no activity

### Stats are aggregates only, and that is not negotiable

Nothing in the API reveals which attendee connected with which. Do not build a UI that
implies it exists or asks for it.

**Everything is suppressed below a cohort of 10** — the response carries
`suppressed: true`. Render that as "not enough activity to report yet", never as zeroes.
Zeroes look like a broken dashboard; the honest message is that we will not report on a
group small enough to identify individuals.

### Billing lives here and only here

Organizer and organization plans are sold on the web. Stripe Checkout, then the webhook
grants entitlements — do not grant anything client-side from a redirect. The user is back
before the webhook lands, so design for "activating…" rather than assuming success.

Consumer Pro is **not** sold here. It goes through StoreKit in the app.

---

## `apps/admin`

Every endpoint exists (`/v1/admin/*`) and every action writes to `audit_log`.

Five rules the API enforces and the UI must not undermine:

1. **Private connection notes are unreachable, even for staff.** There is no endpoint.
   Do not add a feature request for one
2. **Emails are masked in list views.** Full details need a single-record view, which is
   audited. Do not build a bulk export
3. **Destructive actions require a typed id, not a checkbox.** The person using this is
   under pressure and a checkbox is muscle memory
4. **Suspension never deletes connections.** The counterpart did nothing wrong
5. **Unauthorised access returns 404, not 403.** Do not "improve" the error

Access is an allowlist in configuration, so granting it needs a deploy. That is
deliberate — it makes access a reviewable event.

---

## Shared contracts

### Types are generated, never hand-written

```bash
pnpm --filter @acme/api-client generate
```

`packages/api-client` runs on **TypeScript 5.9** via `catalog:codegen`, not TS 7:
`openapi-typescript` cannot run on the Go rewrite, which removed `ts.factory`. Application
code stays on TS 7. This is not an oversight; do not "fix" it.

### Errors are codes, never strings

```json
{"error": {"code": "CARD_LIMIT_REACHED", "message": "...", "details": {"limit": 1},
           "request_id": "01J..."}}
```

`message` is for logs. **Never render it.** Every code needs a string in `en`, `de`, `tr`,
`ar` — registry in `docs/contracts/error-codes.md`.

Show `request_id` on error screens. It is what maps a user's screenshot to a trace.

### Idempotency

Every mutating request carries `Idempotency-Key` (a client-generated UUIDv7). A repeat
returns the original response with `Idempotency-Replayed: true`.

Reusing a key for a *different* body returns `IDEMPOTENCY_KEY_REUSED` — that is a client
bug, not something to retry through.

### i18n

`en`, `de`, `tr`, plus `ar` scaffolded for RTL. Logical properties (`margin-inline-start`)
throughout, `dir="rtl"` on the html element, and **test by using it in `ar`** rather than
by inspecting a screenshot.

---

## Design tokens

`packages/tokens` generates from `tokens.json` via Style Dictionary.

**The current values are placeholders and are meant to be replaced.** They were scaffolded
so the pipeline exists, not because anyone designed them. Replace `tokens.json` wholesale
— the generation pipeline is the part worth keeping, not the values.

Tokens are the only place colours and spacing are defined. Nothing hardcoded, in any of the
four apps.

---

## Definition of done

- [ ] Public card page under 1s on throttled 3G, mid-range Android, cold
- [ ] Card renders with JavaScript disabled
- [ ] Save, share, call, email, link and copy all fire interactions; none send content
- [ ] `noindex` on token pages; slug pages indexable
- [ ] `rel="nofollow ugc"` on every outbound link
- [ ] No raw HTML rendered from card content, sanitized on read
- [ ] SSE reconnects after a network drop and does not accumulate stale state
- [ ] Suppressed stats render as "not enough activity", never zeroes
- [ ] Every error code has a string in all four locales
- [ ] RTL verified in `ar` by using it
- [ ] No consumer-Pro purchase path anywhere on web
- [ ] Admin: no bulk export, no notes, typed confirmation on destructive actions
- [ ] `pnpm --filter @acme/api-client generate` produces no diff against committed types

## Things that will bite

**The public app has no session.** It is anonymous by design. Do not add auth to it "for
analytics" — that changes it from a public page into a tracked one, with the consent
obligations that follow.

**Stripe redirects back before the webhook lands.** Design the return page for
"activating…" with polling, not for immediate success.

**The `noindex` decision is easy to get backwards.** Token pages must not be indexed; slug
pages must be. Getting it the wrong way round either kills the SEO strategy or publishes
non-guessable URLs into Google.
