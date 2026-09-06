"""Models must match the database.

The schema is owned by the migration chain, and the models are hand-written to
match it. Nothing enforces that automatically, so this test does.

Why it matters more than it looks: a drifted model makes `alembic revision
--autogenerate` emit spurious changes on EVERY future migration. People learn
to skim past them, and then a real change hides in the noise. The drift itself
is usually harmless; the habit it creates is not.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from acme import registry  # noqa: F401  (imports every models module)
from acme.core.db import Base

pytestmark = pytest.mark.integration

# Tables intentionally not mapped to a model.
UNMAPPED: frozenset[str] = frozenset({"alembic_version"})

# Alembic's own objects. Note alembic_version's PK index is named
# alembic_version_pkc, not ..._pkey, so the generic pattern below misses it.
# This only shows up when the database was built by Alembic rather than by
# psql, which is exactly how CI builds it.
ALEMBIC_INDEXES: frozenset[str] = frozenset({"alembic_version_pkc"})


async def test_every_table_has_a_model(conn: AsyncConnection) -> None:
    db_tables = (
        set(
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                    )
                )
            ).scalars()
        )
        - UNMAPPED
    )

    mapped = set(Base.metadata.tables)

    missing = db_tables - mapped
    assert not missing, (
        f"tables in the database with no model: {sorted(missing)}. "
        "Add the model, or add the table to UNMAPPED with a reason."
    )


async def test_no_model_without_a_table(conn: AsyncConnection) -> None:
    """The other direction: a model for a table nobody created."""
    db_tables = set(
        (
            await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
        ).scalars()
    )

    orphans = set(Base.metadata.tables) - db_tables
    assert not orphans, (
        f"models with no table: {sorted(orphans)}. Write the migration, or delete the model."
    )


async def test_columns_match(conn: AsyncConnection) -> None:
    """Column names per table, both directions.

    Types are deliberately not compared. Reflected type names differ from
    declared ones in ways that are noise (VARCHAR vs Text, USER-DEFINED for
    every enum), and a strict comparison would fail constantly and get
    disabled. Names catch the mistakes that actually happen: a column added to
    the migration and not the model, or vice versa.
    """
    mismatches: list[str] = []

    def _reflect(sync_conn: object) -> dict[str, set[str]]:
        insp = inspect(sync_conn)
        return {
            table: {c["name"] for c in insp.get_columns(table)}
            for table in Base.metadata.tables
            if insp.has_table(table)
        }

    actual = await conn.run_sync(_reflect)  # type: ignore[arg-type]

    for table_name, table in Base.metadata.tables.items():
        if table_name not in actual:
            continue
        declared = {c.name for c in table.columns}
        in_db = actual[table_name]

        if declared - in_db:
            mismatches.append(f"{table_name}: model has {sorted(declared - in_db)}, db does not")
        if in_db - declared:
            mismatches.append(f"{table_name}: db has {sorted(in_db - declared)}, model does not")

    assert not mismatches, "model/database drift:\n  " + "\n  ".join(mismatches)


async def test_indexes_match(conn: AsyncConnection) -> None:
    """Index names, both directions.

    Partial unique indexes carry real invariants here: the two on `connections`
    are what stop duplicate non-event connections, and the `..._active_idx`
    ones are what let a member rejoin. If a model declares them and the
    database does not, autogenerate will try to create them on the next
    migration.
    """
    declared: set[str] = set()
    for table in Base.metadata.tables.values():
        declared.update(idx.name for idx in table.indexes if idx.name)

    in_db = (
        set(
            (
                await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes"
                        " WHERE schemaname = 'public'"
                        "   AND indexname NOT LIKE '%_pkey'"
                        "   AND indexname NOT LIKE '%_key'"
                    )
                )
            ).scalars()
        )
        - ALEMBIC_INDEXES
    )

    missing_in_db = declared - in_db
    assert not missing_in_db, (
        f"indexes declared on models but absent from the database: "
        f"{sorted(missing_in_db)}. Autogenerate will try to create these."
    )

    missing_in_model = in_db - declared
    assert not missing_in_model, (
        f"indexes in the database but not declared on any model: "
        f"{sorted(missing_in_model)}. Autogenerate will try to DROP these."
    )
