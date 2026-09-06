# @acme/mobile

Expo SDK 57. iOS and Android.

**Expo Go is not sufficient.** NFC and other native modules need a custom dev client. Set
this up on day one rather than discovering it in week six.

## Hard constraints

**No organizer upsell anywhere.** No button, no link, no mention of organizer pricing.
This is an App Store compliance requirement (ADR-0009), and CI has a guard for it
(`scripts/check-no-organizer-upsell.sh`). It will be tempting on the events screen.

**Consumer Pro goes through StoreKit.** Web checkout links only behind a per-region
feature flag, default off.

## Structure (ADR-0026)

```
src/
  features/   cards/ exchange/ connections/ events/ profile/
              mirrors the API domains, so one mental model spans three codebases
  core/
    api/      generated client + TanStack Query setup
    sync/     THE offline outbox. Separate module. Heavily tested.
    db/       expo-sqlite + Drizzle
    auth/ i18n/ telemetry/
  ui/         token-driven primitives
  app/        Expo Router
```

### The distinction that matters most

**TanStack Query is for server state. It is NOT the offline queue.**

TanStack's persistence and mutation retry are built for "this request will probably
succeed shortly". Our requirement is "this exchange happened in a basement, hold it
reliably, send it in order with an idempotency key, possibly tomorrow."

Conflating them loses exchanges, which is the one failure this product cannot have. This
is recorded in ADR-0026 as the most likely mistake.

`core/sync` owns: an outbox table in SQLite, ordered dispatch, exponential backoff,
idempotency keys, conflict resolution. TanStack reads local state; sync reconciles local
against server. Keep the boundary sharp or there are two sources of truth.

**SQLite, not AsyncStorage.** The connections list needs indexed queries, filtering and
search. AsyncStorage is a key-value blob that degrades at a few hundred connections.

## Two QR types (ADR-0002)

- **Live**: in-app screen only, 10-15 min TTL, produces a **symmetric** exchange.
  Pre-mint and cache one so an offline user can still present a valid code. Refresh on
  every foreground.
- **Static**: exported, printed, written to NFC, added to Wallet. **One-way.**

The UI must make this legible without a lecture. That is a UX writing problem and it is
real.

## Offline is the default assumption

Conference wifi fails and basements have no signal. An exchange that fails because of the
network fails in front of another person, at the exact moment the product is meant to
prove itself.

The QR payload carries a compact **unsigned** preview (~120-180 bytes) so the scanner sees
a name immediately. Render it marked **unverified**; the server corrects it on sync. A
signature would cost ~90 bytes and push QR density past the reliable threshold, for a
guarantee the server provides seconds later anyway (ADR-0016).

**Dedicated tests for restart, force-quit and partial sync.** That is where offline
implementations actually fail.

## Conflict resolution

- Card fields: last-write-wins **per field**, not per record
- **Notes: field-level merge with a visible conflict marker. Never silent overwrite.**
  Notes are the highest-value user-authored data in the product.
- Tags: set union, removals tombstoned
- Entitlements, event membership, connection existence: server-authoritative

## Second-user onboarding is the priority path

The five-step flow in the original spec is the *enrichment* flow, not the gate. The common
case is someone installing at an event because a colleague told them to, in a hurry:

```
install -> Sign in with Apple/Google -> name + one field -> card exists -> QR on screen
```

Under 40 seconds on a mid-range Android. Everything else is a dismissible "complete your
card" prompt afterwards. No upfront tutorial.

## Push

**Do not ask for permission on first launch.** Ask after the first successful exchange,
when the value is obvious. iOS only lets you ask once - a cold ask gets ~40% and no
second chance.

**Delivery is best-effort.** Every push has an in-app equivalent backed by the
`notifications` table, so a missed follow-up reminder still appears in the app.

## RTL

`start`/`end` in styles, never `left`/`right` - lint-enforced. Directional icons go
through a mirroring wrapper. **Test in `ar`, not by inspection.**

## Account recovery

The entire value of the app is the user's contacts. Losing them is catastrophic and
produces the worst reviews. Recovery must work from a fresh install with only an email
address, and it is verified on every release.
