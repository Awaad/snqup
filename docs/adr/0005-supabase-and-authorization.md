# ADR-0005: Supabase for Postgres, Auth and Storage; FastAPI owns authorization

**Status:** Accepted
**Date:** 2026-09-04

## Context

Auth provider was undecided between Auth0, Clerk, Kinde, Firebase and a custom
implementation. Cost at scale ruled out Auth0 and Clerk. Custom was considered and
rejected.

Custom auth is not the login form. It is password reset token expiry, timing-safe
comparison, credential stuffing defence, session revocation on password change, and
Apple's specific token handling requirements. That is weeks of work and a permanent
maintenance surface, on a team of one. "Production grade from the first commit" argues
against building it, not for it.

The deciding factor between the managed options was existing familiarity. The team has
production experience with Supabase and none with Firebase. On a solo build, velocity from
familiarity outweighs a marginal feature comparison.

## Decision

**Supabase Pro** provides Postgres, Auth and Storage, in one project.

Auth and application tables live in the same database, so `cards.user_id` is a real foreign
key to a real users table with real cascade behaviour, rather than a mirrored table
reconciled over webhooks.

**Constrained usage.** We use Supabase as managed infrastructure, not as a framework:

- **No PostgREST.** FastAPI is the only thing that talks to the database.
- **No Row Level Security as the primary authorization mechanism.** Authorization lives in
  the FastAPI service layer, where it is testable, version-controlled and reviewable in a
  pull request. RLS may be added later as defence in depth, never as the only defence.
- **No Edge Functions.** Business logic is in one runtime.
- **Supabase Auth is an identity provider only.** It holds credentials and issues JWTs.
  It never sees a card, a connection or an event.

FastAPI verifies JWTs behind a thin adapter interface (`auth/provider.py`). Swapping
providers touches that one module.

Local development uses a Docker Postgres container. Staging and production use Supabase.

## Consequences

**Good.** Backups, point-in-time recovery, connection pooling via Supavisor, and Postgres
patching are someone else's job. These are exactly the runbooks a solo team writes badly
under pressure.

**Good.** Storage with signed URLs covers profile photos, removing a separate object
storage provider from the stack.

**Good.** The exit is cheap: it is Postgres. Our schema, our Alembic migrations, our
queries. `pg_dump` and leave.

**Bad.** $25/month from day one, before revenue.

**Bad.** `auth.users` is the one genuinely sticky element. Migrating away means re-issuing
credentials to every user. The adapter interface reduces but does not eliminate this.

**Bad.** Declining PostgREST and RLS means we write authorization code that Supabase would
have given us. This is intentional — see the tenancy rules in ADR-0018 — but it is real
work.

**Bad.** Connection limits via Supavisor need attention. Async SQLAlchemy opens more
connections than expected under load.

## Alternatives considered

**Firebase Auth as IdP with Postgres elsewhere.** Initially recommended, then reversed on
familiarity. Also required a mirrored users table with webhook reconciliation.

**Self-hosted GoTrue beside our own Postgres.** Free and fully controlled, but adds
database operations to a solo team's workload.

**Custom auth in FastAPI.** Rejected for the reasons in Context.
