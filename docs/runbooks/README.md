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

**Every runbook is currently unverified.** Verifying them is on the Platform team's
definition of done (`handoff/01-platform-infra.md`). Until then this directory is a plan,
not an operations manual.
