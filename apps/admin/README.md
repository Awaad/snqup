# @acme/admin

Internal support tooling. Serves `admin.example.com`.

**Required before launch, not after.** Three runbooks already assume it exists:
`abuse-takedown.md` says "suspend the card, revoke tokens" - with what?
`gdpr-requests.md` assumes a user can be looked up. `handoff/06-billing-entitlements.md`
says conflicts are "surfaced in support tooling".

Without this, every support action is a hand-written SQL statement against production at
the moment of highest pressure. That is how data gets destroyed.

## Separate app, separate domain, on purpose

**Not a route group inside `@acme/web`.** An authorization mistake in a route group
exposes admin functions on a customer-facing surface. A separate deployment with separate
access control makes that class of mistake impossible rather than unlikely.

SSO with a hard staff allowlist, IP allowlist at Cloudflare, short session TTL, no public
registration and no password reset that emails anyone. Unauthenticated access returns
**404, not 401** - do not confirm the app exists.

## Non-negotiable rules

1. **Every action writes to `audit_log`.** No exceptions. That table exists primarily for
   this consumer.
2. **Uses the same API as everything else.** No direct database access. An admin endpoint
   is a normal endpoint with an elevated scope, so it gets the same validation, the same
   soft-delete filtering and the same tests.
3. **Destructive actions require typed confirmation** of the subject identifier. Not a
   checkbox. The person using this is under pressure.
4. **Cannot see private connection notes.** `connection_views` content is not visible to
   staff. If an investigation genuinely needs it, that is a legal process, not a UI
   feature. This preserves the structural guarantee from ADR-0003.
5. **Never display full contact details in list views.** Truncate; reveal on an audited
   single-record view. Staff browsing a contact database is exactly the risk this product
   carries.

## Scope

Deliberately small. Days, not weeks. It can be genuinely ugly - tables and buttons.
Full list in `handoff/09-admin-console.md`.

Card suspension soft-deletes the card and revokes its tokens. It **never** deletes
connections: the snapshot is the counterpart's record of a meeting that happened
(ADR-0004). The console must not offer that option.
