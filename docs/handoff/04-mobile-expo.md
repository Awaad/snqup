# Handoff: Mobile (Expo)

**Owns:** `apps/mobile/`. Expo, React Native, TypeScript.

**Depends on:** Backend (generated client), Design system (tokens).

## Read first

`00-shared-contracts.md`, ADR-0002 (two-tier QR), ADR-0016 (offline sync),
ADR-0011 (i18n/RTL), ADR-0009 (billing placement).

## Hard constraints

**No organizer upsell anywhere in the app.** No button, no link, no mention of organizer
pricing. This is an App Store compliance requirement (ADR-0009), not a design preference.
It will be tempting when building the events screen. Do not.

**Consumer Pro purchase goes through StoreKit.** Web checkout links only behind a
per-region feature flag, default off.

**Expo Go is not sufficient.** NFC and some native modules require a custom dev client.
Set this up on day one rather than discovering it in week six.

## Second-user onboarding is the priority path

The specified five-step onboarding is the *enrichment* flow, not the gate. The common case
is someone installing at an event because a colleague told them to, in a hurry, needing a
working card in 40 seconds.

```
install → Sign in with Apple/Google → name + one field → card exists → QR on screen
```

Photo, headline, socials and themes are deferred to a dismissible "complete your card"
prompt afterwards. If an event code arrived via deep link, the context carries through and
they land already joined.

**No upfront tutorial.** Contextual hints on first use of a screen, if at all.

## Two QR types

The UI must make the distinction legible without a lecture (ADR-0002).

- **Live code**: the in-app screen. Minted fresh, TTL 10–15 min. Pre-mint and cache one so
  the offline user can still present a valid code. Refresh on every foreground.
- **Static code**: what gets exported, printed, written to NFC, added to Wallet. One-way.

## Offline is the default assumption

Conference wifi fails and basements have no signal. An exchange that fails because of the
network fails in front of another person, at the exact moment the product is meant to
prove itself.

- QR payload carries a **compact unsigned preview** (~120–180 bytes) so the scanner sees a
  name immediately. Render it marked **unverified**; the server corrects it on sync.
- **Durable sync queue**: ordered, exponential backoff, survives app restart and
  force-quit. Visible to the user as a pending indicator, never hidden.
- Client generates the UUIDv7, which becomes the server ID. Retries are free.
- `Idempotency-Key` on every mutation.

**Dedicated test coverage for restart, force-quit and partial-sync.** This is where
offline implementations actually fail.

## Conflict resolution (ADR-0016)

- Card fields: last-write-wins **per field**, not per record
- **Notes: field-level merge with a visible conflict marker. Never silent overwrite.**
  Notes are the highest-value user-authored data in the product.
- Tags: set union, removals tombstoned
- Entitlements, event membership, connection existence: server-authoritative

## Meeting context capture

Immediately after an exchange, prompt for a one-line note, typed or voice. Everyone intends
to add notes later and nobody does. Capturing it in the ten seconds after the handshake is
the difference between a useful contact list and a list of names.

## Push (ADR-0024, schema `notifications`)

**Do not ask for permission on first launch.** Ask after the first successful exchange,
when the value is obvious. iOS only lets you ask once — a cold ask gets ~40% and no second
chance.

**Push delivery is best-effort.** Every push has an in-app equivalent backed by the
`notifications` table, so a missed follow-up reminder still appears in the app.

## i18n and RTL

`i18next` + `react-i18next`, `expo-localization` for detection, `I18nManager` for RTL.

- `start`/`end` in styles, never `left`/`right`. Lint-enforced.
- Directional icons mirrored via a wrapper component.
- **Test in `ar`, not by inspection.** Arabic exists specifically so RTL is exercised for
  real.

## Account recovery

The entire value of the app is the user's contacts. Losing them is catastrophic and
produces the worst reviews.

Cloud sync is the default and is not optional for account holders. **Recovery must work
from a fresh install with only an email address**, and it is verified as part of the
release checklist.

## Also required

- In-app account deletion, no support contact, no dark patterns (Apple 5.1.1(v))
- Report and block
- Duplicate detection and merge
- Wallet pass (Apple and Google)
- Empty state for zero connections: an illustration and a "scan your first card" prompt.
  Nothing fabricated.

## Definition of done

- [ ] Second-user path completes in under 40 seconds on a mid-range Android device
- [ ] Offline exchange works in airplane mode and syncs correctly on reconnect
- [ ] Force-quit during sync loses nothing
- [ ] Note conflict produces a visible marker, never silent loss
- [ ] RTL verified in `ar` on both platforms
- [ ] Account deletion works in-app and is discoverable
- [ ] Recovery from fresh install with only an email verified
- [ ] Sentry reports native crashes with readable source maps
- [ ] No organizer upsell present anywhere (grep the bundle)
