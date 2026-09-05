# Runbook: Incident Response

**Last verified:** never

## Severity

| | Definition | Response |
|---|---|---|
| **SEV1** | Users cannot exchange cards, or data is being lost or exposed | Drop everything |
| **SEV2** | A major surface is broken; workaround exists | Same day |
| **SEV3** | Degraded, cosmetic, or affecting few users | Next working day |

**A live customer event escalates severity by one.** A broken dashboard is SEV3 normally
and SEV2 during someone's conference. See `event-day.md`.

## First five minutes

**Do not investigate on a degraded production system.** Stabilise first.

1. **Is a customer event running right now?** Check the events calendar. This changes
   everything about how you proceed.
2. **Can a feature flag fix it?** Turn the surface off in PostHog. Instant, no deploy.
   Try this before anything else.
3. **Did we deploy recently?** If yes, roll back (`rollback.md`) and investigate after.
4. Only then, investigate.

## Triage

```
curl -sf https://api.example.com/health          # process alive?
curl -sf https://api.example.com/health/ready    # dependencies alive?
```

`/health` never touches the database by design, so the two together tell you whether the
problem is the process or a dependency.

Then, in order:
- Sentry: new error class? which release?
- Supabase dashboard: connection count, CPU, disk
- Host: `docker compose ps`, `docker compose logs --tail=200 api`
- Cloudflare: traffic anomaly, attack, or origin errors

## Common failures

**Connection pool exhausted.** Async SQLAlchemy opens more connections than expected.
Symptom: `/health` passes, `/health/ready` times out. Check Supavisor limits. Short term,
restart the API container.

**SSE listener dead, dashboard frozen.** The LISTEN connection must be outside the pooler
(ADR-0006, `handoff/01-platform-infra.md`). Symptom: dashboards connect but never tick.
Restart the API; verify the listener reconnects.

**Valkey out of memory.** Rate limiting and idempotency degrade. Rate limits fail open by
design, so the visible symptom may be an unthrottled endpoint rather than errors. Check
`maxmemory` and eviction stats.

**Job queue backing up.** Reminders and digests stop arriving. Check the worker container
is running and that jobs are not repeatedly failing on one poison message.

**Purge job failed.** Silent and critical. It alerts rather than logging for exactly this
reason. A month of failure is a compliance defect, not a backlog item.

**Safe Browsing warning on the UGC domain.** Go to `abuse-takedown.md`. This is why the
domains are separated (ADR-0008) — it should not affect the API or the app.

## Data exposure

If personal data may have been exposed, this is SEV1 regardless of user count.

1. Stop the exposure — flag off, or take the surface down
2. Determine scope from `audit_log` and request logs
3. Preserve evidence before fixing
4. GDPR: a notifiable breach must reach the supervisory authority **within 72 hours**.
   The clock starts at awareness, not at resolution.
5. Consult counsel before notifying anyone

Do not delete anything while establishing scope.

## Post-incident

Within 48 hours, written down:

- Timeline: what happened, when, what was done
- Root cause, not the proximate symptom
- Why existing monitoring did not catch it earlier
- What changes: code, runbook, or alert
- **Fix any runbook step that was wrong, in the same commit**

Blameless. The purpose is a system that fails less, not a person who is sorry.
