# Runbook: Backup and Restore

**Last verified:** never
**Drill cadence:** quarterly. Put it in the calendar.

## The principle

**A restore you have never run is not a backup.** Supabase's backup configuration screen
is not evidence that recovery works. Only a completed restore is.

## What is backed up

| Data | Mechanism | RPO | Owner |
|---|---|---|---|
| Postgres | Supabase automated + PITR | ~minutes | Supabase |
| Object storage (photos) | Supabase Storage replication | — | Supabase |
| Secrets | SOPS-encrypted in git + age key | commit | **Us** |
| Application config | git | commit | Us |
| **Valkey** | **Not backed up. Deliberate.** | — | — |

Valkey is not backed up because nothing in it is durable (ADR-0007). Total loss must
degrade, never corrupt: rate limits reset, idempotency windows are lost, caches go cold,
jobs are re-enqueued from their Postgres source of truth.

**If a Valkey loss would lose data, that is a bug in the job**, not a gap in backups.

## The highest-severity risk in the system

**The age private key.** Losing it means every secret must be rotated from scratch, under
pressure, with production down.

It must exist in:
1. The operator's password manager
2. A documented offline backup

**Verify both as part of the first deploy**, not later. Verification means decrypting a
real secret with the backed-up key, not confirming the file exists.

## Restore: Postgres point-in-time

1. Supabase dashboard → Database → Backups → PITR
2. Choose the timestamp. **Choose a point before the incident**, not the most recent
   backup — the most recent may already contain the damage.
3. Restore to a **new project**, never over the live one. Overwriting removes your ability
   to compare.
4. Verify the restored data:
   ```sql
   SELECT count(*) FROM connections;
   SELECT max(created_at) FROM connections;
   SELECT count(*) FROM users WHERE deleted_at IS NULL;
   ```
5. Repoint the application connection string, redeploy.
6. Confirm end to end: create a card, scan, verify the connection.

**Expect this to take 30–60 minutes.** Communicate that, do not promise less.

## Restore: partial data loss

If one table or a subset of rows is damaged and the rest is fine, do **not** restore the
whole database — that discards good data written since.

1. PITR restore to a new project
2. Export only the affected rows from the restored copy
3. Import into production inside a transaction
4. Verify before committing

## Quarterly drill

Do all of this, and time it:

- [ ] PITR restore to a scratch project
- [ ] Verify row counts against expectation
- [ ] Decrypt a real secret using the **backed-up** age key, not the one on the server
- [ ] Rebuild the application host from the Compose file plus secrets (target: 30 min)
- [ ] Record the elapsed times here and update `Last verified`

If any step fails or takes materially longer than expected, that is the finding. Fix it
before the next drill.

## Host rebuild

The Hetzner host is a known single point of failure (ADR-0015), accepted at this stage.
Recovery is: provision a new box, pull the Compose file, decrypt secrets with SOPS, start.
Target under 30 minutes.

**This must be drilled**, otherwise the 30-minute figure is a guess and the real number is
discovered during the outage.
