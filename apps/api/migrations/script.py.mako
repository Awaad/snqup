"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Migration checklist (schema/migration-policy.md). Answer these in the PR:

  [ ] Backwards-compatible with the OLDEST supported app version?
      Mobile clients cannot be force-updated. Expand-contract is mandatory.
  [ ] What locks does this take, and for how long on production-sized data?
  [ ] Are new indexes CREATE INDEX CONCURRENTLY inside an autocommit_block?
  [ ] Tested down-migration, or explicitly one-way with a raised error?
  [ ] Touches a table over 1M rows? If so, is it batched?
  [ ] Run against a production-sized copy in staging?
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    # If reversing this would lose data, do NOT write a silent drop. Raise:
    #
    #   raise NotImplementedError(
    #       "Irreversible: drops connection note conflict data. "
    #       "Restore from backup instead. See runbooks/backup-restore.md"
    #   )
    #
    # A down-migration that silently drops a column is worse than none,
    # because it looks safe.
    ${downgrades if downgrades else "pass"}
