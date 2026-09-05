# Handoff: Admin Console

**Owns:** `apps/admin/`.

**Status:** This closes a gap. Several runbooks already assume this tooling exists.

## Why this is not optional

These runbooks are unexecutable without it:

- `runbooks/abuse-takedown.md` — "suspend the card, revoke tokens, record in audit_log."
  With what?
- `runbooks/gdpr-requests.md` — assumes a user can be looked up and an export triggered.
- `handoff/06-billing-entitlements.md` — says subscription conflicts are "surfaced in
  support tooling."
- `00-context/pricing.md` — comped pilot partners are a manual entitlement grant. Someone
  has to grant it.

Without this, every support action is a hand-written SQL statement against production at
the moment of highest pressure. That is how data gets destroyed.

**Required before launch, not after.**

## Separate app, separate domain

`apps/admin`, on `admin.example.com`. **Not a route group inside `apps/web`.**

An authorization mistake in a route group exposes admin functions on a customer-facing
surface. A separate deployment on a separate host with separate access control makes that
class of mistake impossible rather than unlikely.

**Access control:**
- SSO, with a hard allowlist of staff accounts
- IP allowlist at Cloudflare
- Short session TTL
- No public registration path, no password reset flow that emails anyone

## Scope

Deliberately small. It should take days, not weeks. It can be genuinely ugly — tables and
buttons.

### User support
- Lookup by email, user ID, or card slug
- Account state: active, grace period, purge date
- Device and session list, with revoke
- Trigger a GDPR export on behalf of a verified requester

### Safety (`runbooks/abuse-takedown.md`)
- Report queue with triage actions
- Suspend a card: soft-delete plus revoke all its tokens
- Suspend an account
- Add to `reserved_slugs`
- View blocks

**Suspension never deletes connections.** The snapshot is the counterpart's record of a
meeting that happened (ADR-0004). The console must not offer that option.

### Billing (`handoff/06-billing-entitlements.md`)
- Inspect resolved entitlements for a subject, with their source
- **Manual grant with expiry** — this is how comped pilot partners work
- Subscription source conflicts (Apple + Stripe on one user)
- `billing_events` queue: unprocessed, errored, replay

### Events
- Event lookup, attendee count against plan limit
- Consent record inspection before an export dispute
- Force-recompute dashboard aggregates

### Audit
- Search `audit_log` by actor, subject, action, time range

## Non-negotiable rules

**1. Every action writes to `audit_log`.** No exceptions. That table exists primarily for
this consumer. Actor, action, subject, metadata, request ID.

**2. The console uses the same API as everything else.** No direct database access, no
separate query path. An admin endpoint is a normal endpoint with an elevated scope, so it
gets the same validation, the same soft-delete filtering, and the same tests.

**3. Destructive actions require typed confirmation** of the subject identifier. Not a
checkbox. The person using this is under pressure.

**4. It cannot see private connection notes.** `connection_views` content is not visible
to staff. If support genuinely needs it for an investigation, that is a legal process, not
a UI feature. This is the structural guarantee from ADR-0003 and the console must not
undermine it.

**5. Never display full contact details in list views.** Truncate. Reveal on an audited
single-record view. Staff browsing a contact database is exactly the risk this product
carries.

## Definition of done

- [ ] Every action referenced by a runbook is executable in the console
- [ ] Every action writes an `audit_log` entry, verified by test
- [ ] SSO plus IP allowlist enforced; unauthenticated access returns 404, not 401
- [ ] Card suspension revokes tokens and preserves connections
- [ ] Manual entitlement grant works end to end, with expiry
- [ ] `billing_events` replay tested against a real failed delivery
- [ ] Private notes are unreachable, verified by test
- [ ] Deployed to its own subdomain, not a path on the customer app
