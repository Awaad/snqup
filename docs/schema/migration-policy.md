# Migration Policy

## The initial migration is a single baseline

`0001_initial_schema` contains the whole of `schema/schema.sql` in one revision, roughly
600 lines.

Expand-contract exists to protect deployed clients from schema changes. At baseline there
are none. Splitting the initial schema into two dozen revisions manufactures a history
that never ran anywhere, and every one of them has to keep working forever for a
`downgrade base` nobody will use.

**Every change after the baseline is its own migration** under the full policy below.

The DDL is **embedded verbatim** in `0001_initial_schema.py` rather than read from
`schema/schema.sql`. A migration must be immutable: if it read the file, editing that
file would retroactively change what the revision does. `schema/schema.sql` is the
human-readable reference; the migration is the executable truth. A CI check confirms they
have not diverged.

## Tooling

Alembic, from the first table. There is never a hand-edited schema, on any
environment, including staging. `schema/schema.sql` is a reference document; the
migration chain is the source of truth.

## The rule that governs everything

**Old app versions will hit the new schema.**

Mobile clients cannot be force-updated. Users stay on old versions for years. Every
migration must therefore be backwards-compatible with the oldest app version still in
meaningful use.

This makes expand-contract mandatory, not stylistic.

## Expand-contract

Never do a destructive change in one step.

### Renaming a column

```
Release N:    Add new column. Write to both. Read from old.
Release N+1:  Backfill. Read from new. Keep writing to both.
Release N+2:  Stop writing old. Verify no reads for 30 days.
Release N+3:  Drop old column.
```

### Adding a NOT NULL column

```
Release N:    Add nullable with a default.
Release N+1:  Backfill in batches.
Release N+2:  Add the NOT NULL constraint.
```

Never `ADD COLUMN ... NOT NULL` without a default on a large table. It takes an
`ACCESS EXCLUSIVE` lock and rewrites the table.

### Dropping anything

A drop is only permitted after the field has had **zero reads for 30 days**, verified in
logs, and after the oldest supported app version no longer references it.

## Locking rules

Postgres will happily take a lock that stops the world. These are hard requirements:

- `CREATE INDEX` is always **`CONCURRENTLY`**. Never a plain `CREATE INDEX` on a table
  with data.
- `ALTER TABLE ... ADD CONSTRAINT` uses `NOT VALID`, then `VALIDATE CONSTRAINT` in a
  separate transaction.
- Set `lock_timeout` (5s) and `statement_timeout` on migration connections, so a
  migration that would block fails fast instead of queueing every request behind it.
- `CONCURRENTLY` cannot run inside a transaction. Alembic needs
  `with op.get_context().autocommit_block():`.

## Review checklist

Every migration PR answers these in the description:

- [ ] Is this backwards-compatible with the oldest supported app version?
- [ ] What locks does it take, and for how long on production-sized data?
- [ ] Are new indexes `CONCURRENTLY`?
- [ ] Is there a tested down-migration, or is it explicitly one-way?
- [ ] Does it touch a table over 1M rows? If so, is it batched?
- [ ] Has it been run against a production-sized copy in staging?

## Down migrations

Write them. Where a down-migration would lose data, say so explicitly in the docstring
and make it raise rather than silently destroy:

```python
def downgrade() -> None:
    raise NotImplementedError(
        "Irreversible: drops connection note conflict data. "
        "Restore from backup instead. See runbooks/backup-restore.md"
    )
```

A down-migration that silently drops a column is worse than none, because it looks safe.

## Deployment order

1. Migration runs **first**, alone, and must succeed.
2. Application deploys second.
3. Because migrations are expand-only, the old application version keeps working between
   the two steps. This is what makes rollback safe.

Rollback of the application does **not** roll back the migration. That is intentional and
is why expand-contract is mandatory. See `runbooks/rollback.md`.

## Seed data

`reserved_slugs` is seeded from a checked-in list covering route collisions (`api`, `admin`,
`login`, `settings`, `events`, `u`, `c`), profanity, and major brands and public figures.
Seeding is a migration, so every environment has it.
