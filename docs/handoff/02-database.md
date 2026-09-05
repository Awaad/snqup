# Handoff: Database

**Owns:** `apps/api/migrations/`, `schema/schema.sql`, seed data, the purge job.

**Note:** in a small team this is the Backend team wearing a different hat. It is a
separate document because schema changes need Mobile and Web review, and because the
rules here are easy to violate under time pressure.

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
