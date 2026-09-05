# Handoff: Platform and Infrastructure

**Owns:** `infra/`, `.github/workflows/`, Hetzner hosts, Cloudflare, Supabase project
configuration, secrets, monitoring.

**Depends on:** nothing. **This team unblocks everyone else and should start first.**

## Mandate

Every other team's definition of done includes "deploys to staging". Until this team has
CI and staging working, nobody can finish anything. Prioritise accordingly: staging
deploy before production hardening.

## Deliverables in order

### 1. Repository skeleton and CI (blocks everyone)

Monorepo per ADR-0014. Turborepo for task orchestration and affected-package detection —
without it every commit rebuilds the marketing site.

CI on every PR:
- Lint, typecheck, unit tests per affected package
- **Generated-artifact freshness**: regenerate `api-client` and `tokens`, fail if the
  committed output differs. This is the check that prevents contract drift.
- Migration lint against `schema/migration-policy.md`
- `detect-secrets`
- **Placeholder lint**: fail if `acme` appears in a locale file (an i18n defect) or a
  migration (`00-context/naming.md`)

### 2. Environments

Two: `staging` and `production`. Separate Supabase projects, separate Hetzner hosts,
separate Valkey, separate Stripe accounts (test mode), Apple sandbox.

**No shared state of any kind.** A staging job writing to a production bucket is the kind
of incident that ends trust in the whole setup.

### 3. Application host

Hetzner CPX21, Falkenstein. Docker Compose with three services:

```
api      FastAPI behind uvicorn, multiple workers
worker   ARQ, consuming the same image
valkey   maxmemory set, allkeys-lru, AOF everysec
```

**Known footgun, called out because it will otherwise cost a day:** Postgres `LISTEN`
requires a dedicated connection *outside* the pooler. Supavisor in transaction mode will
break the SSE listener (ADR-0006). The listener needs either a direct connection or
Supavisor in session mode. Configure and document this explicitly.

Valkey rules from ADR-0007:
- Nothing in Valkey is durable. Total loss must degrade, never corrupt.
- Rate limiting **fails open with logging**. A false block during a live event is worse
  than an unthrottled hour.

### 4. Cloudflare

In front of everything. WAF, bot filtering, rate-limit pre-filtering, caching.

Three domains per ADR-0008. The UGC domain needs its own zone with distinct rules — it is
the highest-volume, most abuse-prone surface.

Request ID generation happens here and propagates through every service in
`X-Request-ID`.

### 5. Secrets

SOPS with age (ADR-0013).

**The age private key is the highest-severity operational risk in the system.** It must
exist in the operator's password manager and in a documented offline backup. Losing it
means every secret must be rotated from scratch. Verify the backup as part of the first
deploy, not later.

### 6. Deploy

Images built in CI, pushed to GHCR, deployed over SSH.

Order is fixed and matters:
1. Migration runs first, alone, must succeed
2. Application deploys second
3. Health-check-gated rolling replacement

Because migrations are expand-only, the old application keeps working between steps. This
is what makes rollback safe. See `runbooks/deploy.md`.

### 7. Observability

Sentry across three runtimes with release correlation and source maps. PostHog EU.
External uptime monitoring on `/health` every minute.

**Alerts that must page**, not merely log:
- Purge job failure (silent, critical, nobody notices for a month otherwise)
- Billing webhook processing backlog
- Migration failure
- Host down
- Certificate expiry inside 14 days

## Not this team's job

- Application code, business logic, or database schema
- Choosing what to build

## Definition of done

- [ ] A new engineer clones, runs one command, has the full stack locally
- [ ] PR opens → CI runs → merge to `main` → staging deploys automatically
- [ ] Production deploy requires manual approval and completes in under 5 minutes
- [ ] Rollback tested end to end, timed, documented
- [ ] **Restore from backup performed at least once**, not merely configured
- [ ] Age key backup verified by an actual restore
- [ ] All runbooks in `runbooks/` executed once and dated `Last verified`

## Known risks

| Risk | Mitigation |
|---|---|
| Single application host is a SPOF | Accepted at this stage. Documented rebuild under 30 min. Revisit with revenue. |
| Age key loss | Password manager + offline backup, verified |
| Supavisor breaks LISTEN | Documented above; session mode or direct connection for the listener |
| Deploy downtime | Health-gated rolling replacement |
