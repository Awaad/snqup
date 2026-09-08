# Handoff: Mobile (Expo)

**Owns:** `apps/mobile/`. Expo SDK 57, React Native 0.87, TypeScript.

**Status:** the API this app talks to is **built and passing 214 tests**. Every endpoint
below exists today, and `packages/api-client` is generated from its OpenAPI schema. If a
type here disagrees with the generated client, the client is right.

---

# Part 1 — What we are actually building

Read this before the technical sections. Most of the decisions further down only make
sense against it.

## The product in one paragraph

A digital contact identity that works three ways: a **card** you present, a **link** you
share, and a **QR code** someone scans. When two people meet, both ends of the exchange
are recorded with the context of where it happened, and the follow-up is prompted rather
than forgotten. Event organizers get aggregate analytics about the networking at their
event.

## Who it is for, in priority order

**Sales and real estate professionals.** This is the wedge. They meet thirty people at a
trade show, and those contacts have to reach a CRM with context attached or they are
worthless. The current process is: collect paper cards, photograph badges, spend Sunday
night typing them in with no memory of who was who. Agents have been fired over CRM
hygiene. That is a job function, not a nice-to-have.

**Event organizers.** They pay the most, and they are also the distribution channel: one
organizer running a 500-person event puts the app in front of 500 professionals in an
afternoon, with a shared reason to install it at the same moment.

**Everyone else at a conference.** The volume, the free tier, and the reason the public
card page has to work for people who never install anything.

## The one number that matters

```
install → account → first card → first QR shown
       → FIRST SUCCESSFUL EXCHANGE  ← this one
       → returned within 7 days
```

Everything before step five is setup. Everything after is retention. **If a screen does
not move someone toward their first exchange or back into the app afterwards, it is not a
launch screen.**

## Three things that are true and shape every decision

**Most scanners do not have the app.** At a real event, install rates run 2–10%. The
majority of scans hit a web page, not this app. That is why the exchange has to work
one-way, and why `PENDING` states appear everywhere.

**Usage is episodic.** People attend two to four events a year. Between them the app has
no reason to exist unless the connection list, reminders and the link page give it one.
Retention is the hard problem here, not acquisition.

**The moment of use is hostile.** A crowded hall, bad wifi, ninety seconds between
sessions, someone standing in front of you. Every flow is designed for that, not for a
calm evening on the sofa.

---

# Part 2 — The API that exists

## Authentication

Supabase Auth issues the JWT; the API verifies it against Supabase's JWKS and maps the
`sub` claim to our user. Send `Authorization: Bearer <jwt>`.

**Signing keys rotate.** Do not cache anything derived from them. On
`AUTH_TOKEN_INVALID`, refresh through Supabase and retry once.

`AUTH_ACCOUNT_DISABLED` means the account was deleted. The token is still
cryptographically valid — that is exactly why the server checks separately — so treat it
as a hard logout, not a retry.

## Endpoints

```
POST   /v1/cards                              create
GET    /v1/cards                              list
GET    /v1/cards/{id}                         one
PATCH  /v1/cards/{id}                         partial update
DELETE /v1/cards/{id}                         soft delete

POST   /v1/cards/{id}/tokens/live             mint a LIVE token
GET    /v1/cards/{id}/tokens/static           the STATIC token (idempotent)
POST   /v1/cards/{id}/tokens/static/rotate    kill and reissue

POST   /v1/exchanges                          THE exchange

GET    /v1/connections                        cursor-paginated
GET    /v1/connections/{view_id}
PATCH  /v1/connections/{view_id}              note, tags, reminder, archive
POST   /v1/connections/{view_id}/merge
DELETE /v1/connections/{view_id}              your copy only
GET    /v1/connections/duplicates

POST   /v1/events/join                        by code
GET    /v1/events/{id}/stats                  aggregates only
GET    /v1/events/{id}/stream                 SSE, organizer dashboard

GET    /v1/privacy/export                     GDPR. ALWAYS FREE.
DELETE /v1/privacy/account                    in-app deletion
```

**Deliberately absent from this app**, and the absence is a decision rather than an
oversight:

| Endpoint | Why not here |
|---|---|
| `POST /v1/organizations/{id}/events` | Creating an event is a desk task. It needs a name, venue, dates, timezone and visibility — a form nobody wants on a phone between sessions. |
| `POST /v1/events/{id}/roster` | Uploading a registration CSV. Same reason, plus the file is on a laptop. |
| Anything organizer-billing | Organizer plans are sold on the web only. This app must contain no upsell, no link and no mention of organizer pricing — an App Store compliance requirement (ADR-0009), with a CI guard (`scripts/check-no-organizer-upsell.sh`) that fails the build. |

Consumer Pro **does** go through StoreKit in this app. The distinction is the buyer: a
person subscribing for themselves is in-app, a business buying event tooling is web.

`GET /v1/events/{id}/stats` and the SSE stream **are** here, because an organizer standing
in their own venue checking numbers on a phone is a real moment. Creating the event
beforehand is not.

## The two token types

This is the security model. Get it wrong and the product becomes a harvesting tool.

| | Live | Static |
|---|---|---|
| Where | On screen, in-app only | Badge, export, NFC, Wallet, link page |
| Lifetime | 15 minutes | Until rotated |
| Result | **Symmetric** — both get the card | **One-way** — scanner gets the card, owner gets a pending request |
| Why | Presenting it in-app IS the consent | A badge can be photographed without the owner knowing |

**Never export a live token. Never render a static token on the in-app QR screen.**

The server decides which semantics apply from the token itself. You cannot request
symmetry: sending `channel: qr_live` with a static token still produces a one-way
exchange. A live token sent with `channel: nfc` is rejected outright, because a live token
cannot have come from a tag.

## Errors

The API returns **codes, never display strings**:

```json
{"error": {"code": "CARD_LIMIT_REACHED", "message": "...", "details": {"limit": 1},
           "request_id": "01J..."}}
```

`message` is for your logs and must never be shown to a user. Every code needs a string in
all four locale files. Full registry: `docs/contracts/error-codes.md`.

Codes you will actually hit, and what the user should see:

| Code | What happened | What to show |
|---|---|---|
| `TOKEN_EXPIRED` | Live code timed out | "Ask them to show their code again" — not an error |
| `TOKEN_REVOKED` | Their badge was rotated | Same. Not the user's fault |
| `SCAN_SELF` | Scanned own card | Gentle. It is a common mistake |
| `SCAN_BLOCKED` | Either party blocked | Generic failure. **Never reveal a block exists** |
| `CONNECTION_ALREADY_EXISTS` | Duplicate | **Not an error.** Show the existing connection |
| `CARD_LIMIT_REACHED` | Free tier | Upgrade prompt; `details.limit` has the number |
| `CONNECTION_REMINDER_LIMIT_REACHED` | 3 active on free | Upgrade prompt |
| `CARD_LINK_LIMIT_REACHED` | 2 custom links on free | Upgrade prompt |
| `EVENT_CODE_INVALID` | Typo | Re-prompt. Codes exclude `0/O` and `1/I/L`, so do not "helpfully" substitute |

---

# Part 3 — Architecture

Already decided. `docs/adr/0026-client-architecture.md` has the reasoning.

```
apps/mobile/src/
  features/   cards/ exchange/ connections/ events/ profile/
  core/       api/ sync/ db/ auth/ i18n/ telemetry/
  ui/
  app/        Expo Router
```

## The distinction that matters most

**TanStack Query is for server state. It is NOT the offline queue.**

TanStack's retry and persistence assume "this will succeed shortly." The requirement here
is "this exchange happened in a basement, hold it across force-quit, send it in order
tomorrow, never twice." Conflating them loses exchanges, which is the one failure this
product cannot have.

`core/sync` owns a SQLite outbox: ordered dispatch, exponential backoff, idempotency keys,
conflict resolution. The interface is already written in `src/core/sync/types.ts` — read
it first.

**SQLite, not AsyncStorage.** The connection list needs indexed queries, filtering and
search. AsyncStorage is a key-value blob that degrades at a few hundred contacts, and a
sales rep will have thousands.

## Offline is the default assumption

The QR payload carries a compact **unsigned** preview (~120–180 bytes) so the scanner sees
a name immediately. Render it marked **unverified**; the server corrects it on sync.

Every mutation carries a client-generated `Idempotency-Key`. Client-generated UUIDv7 ids
become the server ids, so retries are free.

**Pre-mint and cache a live token** so an offline user can still present a valid code.
Refresh on every foreground.

Test restart, force-quit and partial sync explicitly. That is where offline
implementations actually fail.

## Conflict resolution

- Card fields: last-write-wins **per field**, not per record
- **Notes: field-level merge with a visible marker. Never silent overwrite.** The API
  returns `note_conflict` with both texts; show them and let the user pick
- Tags: set union, removals tombstoned
- Entitlements, event membership, connection existence: server-authoritative

---

# Part 4 — UX guidance

Not a design system. These are the decisions where getting it wrong costs the activation
metric, with the reasoning attached so you can overrule them knowingly.

## The 40-second path

The common install is not a curious person on their sofa. It is someone at an event, told
by a colleague, with a person waiting in front of them.

```
install → Sign in with Apple/Google → name + one field → card exists → QR on screen
```

Under 40 seconds on a mid-range Android. Everything else — photo, headline, socials,
themes — becomes a dismissible "complete your card" prompt afterwards.

**No upfront tutorial.** A tutorial is what you build when the flow is too complex; the
fix is a shorter flow. Contextual hints on first use of a screen, if at all.

If they arrived via an event deep link, carry the context through and land them already
joined.

## The scan screen is the product

It will be opened in a crowded hall, one-handed, in bad light, with someone waiting.

- **Camera opens instantly.** No interstitial, no permission explainer before the system
  prompt. If permission is denied, show manual code entry, not a lecture.
- **The user's own QR is one tap from the scanner.** Exchanges are two-sided and whoever
  moves first is arbitrary. Making someone navigate to "My Card" while a person waits is
  the friction that kills the moment.
- **Maximum brightness while the QR is displayed**, restored on dismiss. Screens get read
  across a table in bad light.
- **Success is unmistakable.** Haptic, plus the other person's name and photo. They need
  to know it worked without reading, because they are about to look back up at a human.
- **Failure never blames the user.** "Ask them to show their code again" beats "Token
  expired."

## Meeting context capture

Immediately after a successful exchange, prompt for one line. Typed or voice.

Everyone intends to add notes later and nobody does. Those ten seconds are the difference
between a useful contact list and a list of names — and that note is the single most
valuable thing we later push to a CRM.

Make it **skippable in one tap**, and never block the next scan behind it. At an event
people scan several times in a row, and a modal interrupting that sequence gets dismissed
reflexively and then permanently.

## Pending connections

Most scans are one-way, so `PENDING` is the common state, not an edge case.

Do not render it as a failure or a half-broken row. It means "they have your card, you
have theirs, they have not confirmed" — which for the user is a normal contact with one
action available. Frame it as an opportunity, not an error.

## Push permission

**Do not ask on first launch.** Ask after the **first successful exchange**, when the
value is concrete: "Remind you to follow up with Sarah?"

iOS permits the prompt once. A cold ask gets roughly 40% and there is no second chance.
That number caps the reminder feature, which is the main reason the app gets opened
between events.

Every push has an in-app equivalent — delivery is best-effort, so a missed reminder must
still be visible in the app.

## The connection list is the retention surface

Between events it is the only reason to open the app.

- **Sort by recency, group by event.** "The 14 people I met at SaaStr" is how people
  actually remember
- **Surface the note prominently.** A row showing "wants a demo of the reporting" is worth
  ten showing a job title
- **A contact with no note is a prompt**, not a neutral row
- **Never hide contacts behind a paywall.** History is free forever. Hiding contacts
  someone already made reads as theft and would generate more one-star reviews than
  everything else combined

## Empty states

The connection list empty state is the first screen most users see after onboarding. Show
what a filled one looks like and point at the scanner. One illustration, one sentence, one
button.

**Never fabricate activity.** No fake counts, no sample contacts that look real.

## RTL

`start`/`end` in every style, never `left`/`right` — lint-enforced. Directional icons go
through a mirroring wrapper.

**Test in `ar`, on a device, by using it.** Arabic ships with English fallback text
specifically so RTL is exercised for real rather than in theory. A layout that looks
mirrored in a screenshot but has a back gesture going the wrong way is not done.

## Account recovery

The entire value of this app is the user's contacts. Losing them is catastrophic and
produces the worst reviews of any failure mode.

Recovery must work from a **fresh install with only an email address**. Verify it on every
release — it is on the release checklist for a reason.

---

# Part 5 — Definition of done

- [ ] Second-user path completes in under 40 seconds on a mid-range Android
- [ ] Offline exchange works in airplane mode and syncs correctly on reconnect
- [ ] Force-quit during sync loses nothing
- [ ] Note conflict shows both texts; nothing is silently overwritten
- [ ] Live token never exported; static token never on the in-app QR screen
- [ ] Every error code has a string in `en`, `de`, `tr`, `ar`
- [ ] RTL verified in `ar` by using the app, not by inspecting a screenshot
- [ ] Push permission asked after the first exchange, never on launch
- [ ] Account deletion reachable in-app, no support contact, no dark patterns
- [ ] Recovery from a fresh install with only an email verified
- [ ] Sentry reports native crashes with readable source maps
- [ ] No organizer upsell anywhere (`scripts/check-no-organizer-upsell.sh` passes)

## Things that will bite

**Expo Go is not sufficient.** NFC and other native modules need a custom dev client. Set
it up on day one, not in week six.

**The app cannot be force-updated.** Old versions run for years, so tolerate unknown enum
values on read and never assume a field is present because it is present today.

**Store review will scrutinise this app.** Guideline 5.1.2 covers sharing third-party
data, which is this app's entire function. Consent flows must be visible in the UI rather
than buried in terms, and account deletion must be in-app (5.1.1(v)). Budget for a
rejection.
