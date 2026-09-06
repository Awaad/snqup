# @acme/public

**The highest-traffic surface in the product, and the one that does the growth.**

Serves `example.net` - a separate registrable domain from the app and marketing site.

## Why the separate domain

Any product where strangers create pages with their own name, photo and outbound links is
a phishing host. Someone registers as "PayPal Security", writes "verify your account
here", and mails the URL to ten thousand people.

When enough get reported, Safe Browsing blocklists **the domain**, not the path. Every
link in the product then shows a red interstitial in Chrome, Safari, Gmail, WhatsApp and
Slack simultaneously. Delisting takes days and recurs (ADR-0008).

## Two pages, often conflated. They are not the same.

**`/u/<slug>` - link-in-bio.** Genuinely public, guessable, indexable. Server-rendered on
request against live data with a short cache. **Not statically generated**: a card edit
must appear immediately.

**`/c/<token>` - scan resolution.** **Not guessable.** Opaque token, always `noindex`,
revocable - so a leaked badge photo is fixed by rotating the token rather than reprinting
five hundred badges.

## The scan page is the growth loop

Most scanners will not have the app. This page must be flawless:

- **vCard save** - one tap, contact in their phone. The primary action.
- **Wallet add**
- Optional reply form -> pending exchange, emails them the card, invites them to claim.
  **Optional**: forcing it kills the save flow.
- Install prompt is **secondary**, never a wall.

## Performance budget

**Under 1 second on hotel wifi**, mid-range phone. Aggressive Cloudflare caching, minimal
JavaScript.

This app shares design tokens with `@acme/web` and **nothing else**. Do not import the
dashboard's data layer, component library or state management. Reusing them is the
obvious convenience and it is exactly how the budget is lost (ADR-0026). Expect to defend
this in review; the pressure will be constant.

## Controls

`noindex` by default, `rel="nofollow ugc"` on every outbound user link, link scanning on
save, and a reachable takedown path (`runbooks/abuse-takedown.md`).
