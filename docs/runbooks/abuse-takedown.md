# Runbook: Abuse and Takedown

**Last verified:** never

## Why this matters more than it looks

The UGC domain is a phishing host by construction. Someone registers as "PayPal Security",
writes "verify your account here", adds a link, and mails the URL to ten thousand people.
The target sees our domain, which is what makes the attack work.

If enough of these are reported, **Safe Browsing blocklists the domain**, and then every
link on it shows a red interstitial in Chrome, Safari, Gmail, WhatsApp and Slack
simultaneously. Delisting takes days and recurs.

Domain separation (ADR-0008) contains the blast radius. Fast takedown is what prevents the
listing in the first place.

## Intake

- In-app report → `reports` table
- `abuse@` email → must reach a human, monitored daily
- Safe Browsing / SmartScreen warning → treat as SEV2 immediately

## Triage

| Type | Action | Speed |
|---|---|---|
| Phishing / brand impersonation | Suspend card, revoke tokens | **Immediate** |
| Impersonating a person | Suspend pending verification | Immediate |
| Spam links | Remove links, warn | Same day |
| Harassment | Suspend, review | Same day |
| Adult / illegal content | Suspend, preserve evidence, consider reporting | **Immediate** |
| Disputed content | Review, respond to reporter | 3 days |

**Err toward suspension for anything on the UGC domain.** Wrongly suspending one card is a
support conversation. A Safe Browsing listing takes down every link in the product.

## Suspending a card

1. Soft-delete the card (`cards.deleted_at`)
2. Revoke all its tokens (`card_tokens.revoked_at`) — the card is unresolvable but existing
   connection snapshots survive, which is correct
3. Record in `audit_log`
4. Notify the owner with the reason and an appeal path

Existing connections are **not** deleted. The snapshot is the counterpart's record of a
meeting that happened (ADR-0004).

## Impersonation

Our verification mechanism is **domain control**, not KYC. Storing government ID is a
compliance burden and a breach risk that dwarfs everything else in this product.

- Claiming a company → verify the email domain (`domain_verifications`)
- A company claiming its name → organization verification, which then controls who may
  display it
- Genuinely high-profile individuals → manual review, case by case. Do not build a system
  for a problem with five instances.

## Safe Browsing listing

This is SEV2 and it affects every link on the affected domain.

1. Google Search Console → Security Issues. Identify the flagged URLs.
2. Remove or suspend the offending content
3. Request a review through Search Console
4. **Expect days, not hours**
5. While listed, communicate to affected users. Their cards are unreachable and they will
   assume the product is broken.

Then: what let it through? Link scanning on save, `rel="nofollow ugc"`, and `noindex`
defaults are the preventive controls. If one failed, fix it.

## Reserved slugs

`reserved_slugs` blocks route collisions (`api`, `admin`, `login`, `settings`, `events`,
`u`, `c`), profanity, and major brands and public figures. Seeded by migration.

**Add to it proactively.** A brand added after someone registers it is a dispute; added
before, it is nothing.

## Blocks

User-level blocks (`blocks` table) prevent exchange in both directions. `SCAN_BLOCKED` is
returned. Blocks are private — never reveal to the blocked party that they were blocked.

## Records

Every action in `audit_log`: what, who, why, when, and the appeal outcome. This is the
evidence trail if a decision is challenged.
