# ADR-0008: Three-domain separation for UGC isolation

**Status:** Accepted
**Date:** 2026-09-04

## Context

Three surfaces in this product are served over the web with user-generated content:
link-in-bio pages, scan-resolution pages, and public event pages. Each lets a stranger
create a page carrying their own name, photo, free-text bio and outbound links.

That is a phishing host. Someone registers as "PayPal Security", writes "verify your
account here", adds a link, and mails the URL to ten thousand people. The target sees our
domain in the address bar, which is precisely what makes the attack work.

When enough of these are reported, Google Safe Browsing and Microsoft SmartScreen
frequently blocklist **the domain**, not the path. At that point Chrome, Safari, Firefox,
Gmail, WhatsApp, iMessage and Slack all show a red interstitial for every link in the
product, including the marketing site and the API if it shares the apex. Delisting takes
days and recurs. Linktree, Bitly and Notion have all been through this.

## Decision

Three separate registrable domains.

| Domain | Serves | UGC? | Indexed? |
|---|---|---|---|
| `example.com` | Marketing site, blog | **No** | Yes |
| `app.example.com`, `api.example.com` | Web app, organizer dashboard, API | No | No |
| `example.net` | Public card pages, scan resolution | **Yes** | Selectively |

**Public event pages are the exception** and live at `example.com/events/...`, on the
primary domain. They are gated behind organizer verification (a verified email domain or a
paid plan), which makes them curated and low-risk, and it is where we want SEO equity.
Nobody pays to host a phishing page, so the paywall doubles as an anti-spam filter.

Accompanying controls on the UGC domain:

- `noindex` by default. Indexing requires an account trust signal.
- `rel="nofollow ugc"` on every outbound user link.
- Link scanning on save against a reputation API.
- A reachable `abuse@` address and a documented takedown path
  (`runbooks/abuse-takedown.md`).
- Scan-resolution URLs use opaque tokens, are never guessable, and are always `noindex`.

## Consequences

**Good.** A Safe Browsing hit on the UGC domain does not take down the API, the app, the
marketing site, or event pages. The blast radius is contained to the surface that earned
it.

**Good.** Cookie isolation between the UGC domain and the authenticated app comes free,
which removes a whole class of session attack.

**Bad.** Two additional domain registrations and two more certificate lifecycles.

**Bad.** Cross-domain flows (a scanner on the UGC domain who wants to install and claim
their pending connection) need careful handling, since no shared cookie exists. Handled
with signed, single-use claim tokens.

**Bad.** SEO equity is split. The UGC domain builds little authority. Accepted: individual
long-tail event pages rank, and those are on the primary domain.

## Alternatives considered

**Everything on one domain with path separation.** Rejected: Safe Browsing operates on
domains. This is the failure mode being avoided.

**Subdomain per user (`sarah.example.com`).** Rejected: wildcard certificate complexity,
and subdomain reputation is often still inherited by the apex.
