# Shared Contracts

**Status: the backend is built.** 32 endpoints, 227 tests, four enforced import
contracts. `packages/api-client` is generated from its OpenAPI schema and is the
authority on request and response shapes. Where a handoff and the code disagree, the code
is right — open a PR to fix the handoff in the same change that noticed.

Every handoff now carries its own status line. Read it before trusting the rest.

**Every team reads this before their own handoff.** These are the rules that let five teams
build in parallel without meeting. Breaking one of them breaks someone else's work,
usually silently.

## The repository

```
apps/
  api/          FastAPI          — Backend team
  mobile/       Expo             — Mobile team
  web/          Next.js          — Web team (authenticated app + organizer dashboard)
  public/       Next.js          — Web team (UGC domain: card pages, scan resolution)
  marketing/    Next.js          — Web team (primary domain, no UGC)
packages/
  api-client/   GENERATED        — nobody edits by hand
  tokens/       GENERATED        — Design system team owns the source
  shared/       Hand-written     — cross-platform validation and formatting
infra/                           — Platform team
docs/                            — everyone
```

## Ownership

| Path | Owner | Others may |
|---|---|---|
| `apps/api/**` | Backend | Read, open issues |
| `apps/mobile/**` | Mobile | Read |
| `apps/web/**`, `apps/public/**`, `apps/marketing/**` | Web | Read |
| `packages/tokens/tokens.json` | Design system | Propose via PR |
| `packages/api-client/**` | **Nobody.** Generated. | Regenerate only |
| `infra/**`, `.github/**` | Platform | Propose via PR |
| `docs/adr/**` | Anyone may propose; product owner accepts | |
| `docs/schema/**` | Backend, but changes need Mobile + Web review | |

`CODEOWNERS` enforces this. Cross-boundary PRs need the owning team's approval.

## Non-negotiable rules

These are not style preferences. Each one exists because violating it causes a specific,
known failure.

**1. Never hand-edit generated code.** `packages/api-client` and `packages/tokens` output
are regenerated in CI and the build fails if committed output differs. A hand edit is
silently reverted.

**2. Backend returns error codes, never user-facing strings** (ADR-0011). Adding a code
means adding it to `contracts/error-codes.md` and all four locale files in the same PR.

**3. Every mutating request carries `Idempotency-Key`.** Offline retries make duplicates
the normal case (ADR-0016).

**4. All timestamps are `timestamptz`, UTC, RFC 3339.** Events additionally store an IANA
timezone string. Never an offset — DST makes offsets wrong twice a year.

**5. Money is integer minor units with a currency code.** Never a float. **Never store a
price** — prices come from StoreKit or Stripe at render time (ADR-0009).

**6. Soft delete everywhere.** `deleted_at` on every user-facing table, filtered by the
repository layer.

**7. Styles use `start`/`end`, never `left`/`right`.** Web uses CSS logical properties.
Lint-enforced (ADR-0011).

**8. No user-facing string is ever typed inline.** Everything goes through i18n, including
the product name, which is `{{productName}}` (`00-context/naming.md`).

**9. Never log an email, phone number, or token.** This product's logs would otherwise be
a contact database. Enforced by a redaction processor, but code review must watch for
whole-object logging (ADR-0024).

**10. Internal UUIDs are never exposed on public unauthenticated surfaces.** They leak
creation timestamps (ADR-0010).

**11. `deleted_at` is not erasure.** The purge job is what satisfies GDPR (ADR-0020).
Anything that writes personal data must be reachable by the purge.

**12. The mobile app contains no organizer upsell.** No button, no link, no mention. This
is an App Store compliance requirement, not a preference (ADR-0009).

## Definition of done

A feature is done when **all** of these are true. No exceptions for "we'll add tests
later."

- [ ] Behaviour matches the handoff, or the handoff was updated first
- [ ] Error paths return registered codes; strings exist in all four locales
- [ ] Idempotency on mutating endpoints
- [ ] Soft delete respected; purge reaches any new personal data
- [ ] Tests: exhaustive on exchange and entitlements, meaningful elsewhere (see below)
- [ ] Instrumented: Sentry context, PostHog events where the funnel touches it
- [ ] No new lint or type errors; generated artifacts regenerated
- [ ] RTL verified if UI (test in `ar`, not by inspection)
- [ ] Feature-flagged if it is a new surface (ADR-0022)
- [ ] Migration is expand-contract and reviewed against `schema/migration-policy.md`

## Testing policy

Deliberately uneven. Uniform coverage targets produce tests that exist to hit a number.

**Exhaustive coverage, no exceptions:**
- The exchange endpoint. Every path: live token, static token, offline replay, duplicate,
  concurrent scan of the same pair, blocked user, expired token, self-scan.
- The entitlements resolver. Every source, conflict, expiry and grace combination.
- Tenant isolation. A test suite that proves cross-tenant reads are impossible.
- The purge job. It is silent, critical, and nobody notices when it fails.

**Meaningful coverage:** business logic in the service layer, sync conflict resolution,
migration up/down.

**Light coverage:** presentational components, CRUD wrappers with no logic.

**Not covered:** generated code, third-party SDK behaviour.

## Branching and CI

`main` is always deployable. Short-lived branches, squash merge, conventional commits.

CI on every PR: lint, typecheck, unit tests, generated-artifact freshness, migration
lint, `detect-secrets`. Merge to `main` deploys to staging automatically. Production
requires manual approval.

## Communication

- A question about someone else's layer → issue on their area, not a direct message.
  The answer is then written down.
- A decision affecting more than one layer → ADR before implementation.
- A discovered gap in a handoff → fix the handoff in the same PR.

## Environments

| | Local | Staging | Production |
|---|---|---|---|
| Postgres | Docker | Supabase (separate project) | Supabase |
| API | uvicorn reload | Hetzner staging host | Hetzner prod host |
| Web | `next dev` | Vercel preview | Vercel prod |
| Mobile | Expo dev client | TestFlight / internal track | Store |

**No shared state between staging and production, ever.** Not a database, not a bucket,
not a Stripe account, not a Valkey instance. Use Stripe test mode and the Apple sandbox in
staging.
