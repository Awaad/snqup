# Runbooks

Procedures written to be followed at 3am by someone who did not write them.

## Rules

**A runbook that has never been executed is a draft.** Each carries a `Last verified`
date. If it is more than 90 days old, treat it as untrusted and verify before relying on
it.

**Update the date when you run it.** If a step was wrong, fix the step in the same commit.

**No runbook says "and then figure it out".** If a procedure has an unknown, that is a gap
to close, not a step.

## Index

| Runbook | When | Verified |
|---|---|---|
| `deploy.md` | Every production release | **Never** |
| `rollback.md` | A deploy went wrong | **Never** |
| `backup-restore.md` | Data loss, or quarterly drill | **Never** |
| `incident-response.md` | Production is degraded or down | **Never** |
| `secret-rotation.md` | Scheduled, or after exposure | **Never** |
| `gdpr-requests.md` | A data subject request arrives | **Never** |
| `abuse-takedown.md` | A report or a Safe Browsing warning | **Never** |
| `event-day.md` | A customer is running a live event | **Never** |
| `rename-product.md` | Once, when the name is chosen | **Never** |

## Backing-code audit, 2026-09-07

Each runbook was checked against the implementation, because a procedure that says
"suspend the card and revoke its tokens" is fiction until something can do that. Every
capability now exists:

| Runbook | Needs | Status |
|---|---|---|
| `abuse-takedown` | card suspension, token revocation, audit writes, report queue, reserved slugs | all present |
| `gdpr-requests` | free export, in-app deletion, purge job, user lookup | all present |
| `deploy` / `rollback` | deploy script, readiness gate, migration lock timeout | all present |
| `event-day` | SSE dashboard, attendee limit check, post-event digest | all present |
| `secret-rotation` | multi-key JWT accept, JWKS rotation, webhook secret config | all present |
| `incident-response` | billing reconciliation, stuck-webhook view | all present |

**Backed by code is not the same as verified.** Every runbook still carries
`Last verified: never`, because none has been executed end to end against real
infrastructure. Doing that is on the Platform team's definition of done
(`handoff/01-platform-infra.md`), and until then this directory is a plan rather than an
operations manual.

The distinction matters most for `backup-restore`: a restore that has never been run is
not a backup, and no amount of application code changes that.
