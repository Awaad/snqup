# Handoff: Database

**Owns:** `apps/api/migrations/`, `schema/schema.sql`, seed data, the purge job.

**Note:** in a small team this is the Backend team wearing a different hat. It is a
separate document because schema changes need Mobile and Web review, and because the
rules here are easy to violate under time pressure.

## Running it locally

```bash
cp .env.example .env            # DATABASE_URL lives here
docker compose up -d --wait     # pgvector/pgvector:pg17 + Valkey
pnpm api:migrate                # alembic upgrade head
pnpm api:test
```

Alembic and pytest resolve the database through the **same** narrow settings model
(`acme.core.config.DatabaseSettings`): environment first, then `.env`. Neither has a
fallback, so they cannot disagree about which database they mean and a missing variable
says so instead of pointing at one that does not exist.

Set `TEST_DATABASE_URL` to run the suite against a separate database. Leaving it unset is
fine: every fixture runs in a transaction that is rolled back.

The image is **`pgvector/pgvector:pg17`**, not plain `postgres`. The baseline enables the
`vector` extension, and `CREATE EXTENSION` fails outright when it is unavailable —
`IF NOT EXISTS` does not help. `postgres:17-alpine` cannot run our migration at all.

Migrations run **synchronously on psycopg** while the app runs on asyncpg. asyncpg sends
every statement as a prepared statement and PostgreSQL refuses multiple commands in one,
so a 600-line baseline fails outright on it. Alembic has no reason to be async.

## Source of truth

The Alembic chain is executable truth. `schema/schema.sql` is the reference document. If
they disagree, the SQL file is stale and is fixed in the same PR that found the gap.

## Read first

- `schema/schema.sql` — the whole schema with reasoning in comments
- `schema/migration-policy.md` — expand-contract, locking, review checklist
- ADR-0003 (connection model), ADR-0010 (identifiers), ADR-0020 (data lifecycle)

## The five things most likely to be got wrong

**1. The two partial unique indexes on `connections`.** Postgres treats NULLs as distinct
in unique indexes. A single constraint over a nullable `event_id` permits unlimited
duplicates for non-event connections. Two partial indexes, one `WHERE event_id IS NOT
NULL`, one `WHERE event_id IS NULL`. Do not "simplify" this.

**2. Canonical ordering on connections.** `CHECK (user_low_id < user_high_id)`. Without
it the same pair is storable twice reversed and the unique indexes do nothing. Application
code must order the pair before insert.

**3. Private notes are in `connection_views`, never on `connections`.** One row per
participant. This is the structural guarantee that a query bug cannot leak A's private
note to B (ADR-0003). Any PR that moves note data onto the edge is rejected.

**4. Never a plain `CREATE INDEX`.** Always `CONCURRENTLY`, which means
`autocommit_block()` in Alembic. A plain index build on a production table takes an
`ACCESS EXCLUSIVE` lock.

**5. Columns marked UNUSED IN v1 are not dead code.** `discoverable_at`,
`discovery_prefs`, `connections.visibility` exist so v1.1 discovery needs no migration
(ADR-0023). They are commented in the schema pointing at the ADR.

## Models are hand-written and drift is a test failure

The schema is owned by the migration chain; models are written to match it. Nothing
enforces that automatically, so `tests/test_model_schema_sync.py` does — tables, columns
and indexes, in both directions.

This matters more than it looks. A drifted model makes `alembic revision --autogenerate`
emit spurious changes on **every** future migration. People learn to skim past them, and
then a real change hides in the noise.

It has already earned its place: it caught `organizations.slug` having no uniqueness at
all, because a schema edit silently applied half of what was intended (finding 10 in
`schema/review-2026-09-05.md`).

`registry.py` imports every models module so `Base.metadata` is complete. Without
it, the drift test compares an incomplete picture and passes while half the schema is
unmapped.

## Schema invariants are tested, not assumed

`apps/api/tests/test_schema_invariants.py` enforces every finding from
`schema/review-2026-09-05.md`. Run it before and after any migration.

Two of those tests are **structural**: one asserts the exact column set of `users`, the
other compares `information_schema` columns against triggers. They fail when someone adds
something wrong rather than when they break something, which is how these defects get
reintroduced. If a legitimate change makes one fail, update the test in the same PR and
say why — do not delete it.

## The purge job

The highest-risk piece of code in this layer, because it is silent.

- Runs daily. Hard-deletes soft-deleted rows past `purge_after`.
- Must reach **every** table holding personal data. A new table holding personal data that
  the purge does not reach is a compliance defect, not a backlog item.
- **Must alert on failure, not merely log.** If it fails for a month nobody notices until
  an audit.
- Exhaustively tested (`00-shared-contracts.md` testing policy).
- Card snapshots are **retained** on counterpart erasure, per the documented position in
  ADR-0020. This is deliberate. Do not "fix" it.

## Seed data

`reserved_slugs`, seeded by migration so every environment has it: route collisions
(`api`, `admin`, `login`, `settings`, `events`, `u`, `c`), profanity, major brands and
public figures.

## Definition of done

- [ ] Every migration reviewed against the checklist in `schema/migration-policy.md`
- [ ] Every migration run against a production-sized copy in staging
- [ ] Down migrations written, or explicitly raising `NotImplementedError` with a reason
- [ ] Purge job exhaustively tested and alerting on failure
- [ ] Tenant isolation suite passes: cross-tenant reads structurally impossible
- [ ] `schema/schema.sql` matches the migration chain
