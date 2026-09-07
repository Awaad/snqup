"""seed reserved slugs

Revision ID: 0002_seed_reserved_slugs
Revises: 0001_initial_schema
Create Date: 2026-09-07

Seeds the reserved slug list so every environment has it. Without this, local
and staging happily issue `/u/admin` and only production refuses it, which is
the worst place to discover a routing collision.

The list lives in the data package and is imported below rather than
duplicated here. That means this migration is NOT frozen the way DDL is: re-running
it picks up additions, which is the point - the list grows and a brand added
after someone registers it is a dispute with a real person.

Idempotent, so a redeploy or a later addition is safe.

Migration checklist (schema/migration-policy.md):

  [x] Backwards-compatible: inserts only, no schema change.
  [x] Locks: row inserts into a small table.
  [x] Indexes: none added.
  [x] Down-migration: removes only the slugs this seed inserted.
  [x] Tables over 1M rows: no.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from acme.data.reserved_slugs import RESERVED

revision: str = "0002_seed_reserved_slugs"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not RESERVED:
        return

    reserved_slugs = sa.table(
        "reserved_slugs",
        sa.column("slug", sa.Text),
        sa.column("reason", sa.Text),
    )
    # ON CONFLICT DO NOTHING: this runs again whenever the list grows, and an
    # existing row must not fail the deploy.
    op.execute(
        sa.dialects.postgresql.insert(reserved_slugs)
        .values([{"slug": s, "reason": r} for s, r in sorted(RESERVED.items())])
        .on_conflict_do_nothing(index_elements=["slug"])
    )


def downgrade() -> None:
    """Remove only what this seed added.

    A blanket DELETE would also drop slugs an operator reserved by hand through
    the admin console, which is a support action with a reason attached
    somewhere in audit_log.
    """
    if not RESERVED:
        return
    op.execute(
        sa.text("DELETE FROM reserved_slugs WHERE slug = ANY(:slugs)").bindparams(
            sa.bindparam("slugs", sorted(RESERVED), type_=sa.ARRAY(sa.Text))
        )
    )
