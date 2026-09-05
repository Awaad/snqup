# Runbook: Deploy

**Last verified:** never
**Typical duration:** 5 minutes
**Rollback:** `rollback.md`

## Preconditions

- [ ] CI green on `main`
- [ ] Release checklist complete (`handoff/08-qa-release.md`)
- [ ] Migration reviewed against `schema/migration-policy.md`
- [ ] Not deploying during a customer's live event — check the events calendar. A deploy
      during someone's conference is the worst possible timing.

## Order is fixed and matters

1. **Migration runs first, alone, and must succeed.**
2. **Application deploys second.**

Because migrations are expand-only, the old application version keeps working between the
two steps. This is what makes rollback safe. Never reverse the order, never combine them.

## Procedure

### 1. Deploy to staging

Automatic on merge to `main`. Wait for it. Verify:

```
curl -sf https://api-staging.example.com/health
curl -sf https://api-staging.example.com/health/ready
```

Smoke test on staging: create a card, scan it, verify the connection appears on both
sides.

### 2. Run the migration against production

```
gh workflow run migrate.yml -f environment=production
```

Watch it. `lock_timeout` is set to 5s, so a migration that would block fails fast rather
than queueing every request behind it. **If it fails on lock timeout, do not retry
blindly** — find what is holding the lock:

```sql
SELECT pid, state, wait_event_type, left(query, 80)
FROM pg_stat_activity
WHERE state <> 'idle' ORDER BY query_start;
```

### 3. Deploy the application

```
gh workflow run deploy.yml -f environment=production
```

Requires manual approval on the production environment.

The script performs health-gated rolling replacement: new container starts, `/health/ready`
must pass, then traffic shifts, then the old container stops. If readiness never passes,
the deploy aborts and the old container keeps serving.

### 4. Verify

```
curl -sf https://api.example.com/health
curl -sf https://api.example.com/health/ready
```

- [ ] Sentry: no new error classes in the first 5 minutes
- [ ] A real scan resolves on the public domain
- [ ] SSE dashboard connects and receives a tick
- [ ] Worker is consuming (check queue depth is not growing)

### 5. Web

Vercel deploys on merge. Verify the scan-resolution page loads and a vCard downloads.

### 6. Mobile

Separate cadence, gated by store review. Never assume the app and the API ship together —
old app versions will hit the new API. That is why migrations are expand-contract.

## If something looks wrong

Do not investigate on production while it is degraded. Roll back first
(`rollback.md`), investigate second. The rollback is fast and reversible; a long
investigation with users affected is not.
