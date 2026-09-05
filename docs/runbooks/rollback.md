# Runbook: Rollback

**Last verified:** never
**Target:** under 3 minutes
**Principle:** roll back first, investigate second.

## What rollback does and does not do

**Rolls back:** the application container, to the previous image tag.

**Does NOT roll back:** the database migration. This is deliberate. Expand-contract
(`schema/migration-policy.md`) means the previous application version works against the new
schema, so rolling back the application alone is safe.

**If a migration itself is the problem**, that is a different and much worse situation.
See below.

## Application rollback

```
gh workflow run deploy.yml -f environment=production -f image_tag=<previous-sha>
```

The previous tag is in the deploy workflow history. Find it before you need it.

Verify:
```
curl -sf https://api.example.com/health/ready
```

Then confirm in Sentry that the error rate has returned to baseline.

## Web rollback

Vercel dashboard → Deployments → previous deployment → Promote to Production. Roughly 30
seconds.

## Feature flag rollback (usually the right answer)

If the problem is one surface rather than the whole application, **turn the flag off in
PostHog instead of rolling back.** Instant, no deploy, no downtime, and it is why every
major surface is flagged (ADR-0022).

Try this first. It is faster and less disruptive than a container rollback.

## Migration rollback

Only if the migration itself is the fault.

1. Check whether a down-migration exists and whether it raises `NotImplementedError`.
   Irreversible migrations say so explicitly with a reason.
2. If reversible:
   ```
   gh workflow run migrate.yml -f environment=production -f target=<previous-revision>
   ```
3. **If irreversible**, this is a restore-from-backup situation. Go to
   `backup-restore.md`. Do not improvise a fix against production data.

## Mobile rollback

You cannot roll back a released app version. Options, in order of preference:

1. **Turn off the feature flag.** This is why flags exist.
2. **Fix on the server.** Old clients must keep working anyway.
3. Expedited store review, which is days, not minutes.

This asymmetry is the reason mobile compatibility is a hard constraint on every API and
schema change.

## After any rollback

- [ ] Write down what happened while it is fresh
- [ ] Sentry issue linked to the incident
- [ ] `incident-response.md` post-incident section
- [ ] If a runbook step was wrong, fix it in the same commit
