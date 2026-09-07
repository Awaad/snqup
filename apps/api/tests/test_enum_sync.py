"""Python enums must match the database CHECK constraints.

The schema uses `text` plus a named CHECK rather than Postgres enums (ADR-0027),
so the set of permitted values lives in a constraint definition and the Python
StrEnums are hand-written to match.

Drift is nasty in both directions and neither fails at import:

  value in the DB but not Python  -> LookupError the first time a row carrying
                                     it is READ, in production, on whichever
                                     code path touches it first
  value in Python but not the DB  -> the write fails with a constraint
                                     violation, which at least fails loudly

Constraints are named `<table>_<column>_check_values` so this test can find
them and so a violation message names the column.
"""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from acme.domains.billing.enums import EntitlementSource, EntitlementStatus, SubjectKind
from acme.domains.cards.enums import CardKind, TokenKind
from acme.domains.connections.enums import (
    ConnectionState,
    ConnectionVisibility,
    InteractionKind,
    ScanChannel,
)
from acme.domains.crm.enums import CrmProvider
from acme.domains.events.enums import EventStaffRole, EventVisibility
from acme.domains.identity.enums import OrgRole
from acme.domains.notifications.enums import NotificationKind
from acme.domains.safety.enums import ReportStatus

pytestmark = pytest.mark.integration

# (table, column) -> the Python enum that mirrors its CHECK constraint.
ENUMS = {
    ("cards", "kind"): CardKind,
    ("card_tokens", "kind"): TokenKind,
    ("connections", "channel"): ScanChannel,
    ("connections", "state"): ConnectionState,
    ("connections", "visibility"): ConnectionVisibility,
    ("anonymous_scans", "channel"): ScanChannel,
    ("scan_interactions", "kind"): InteractionKind,
    ("notifications", "kind"): NotificationKind,
    ("crm_connections", "provider"): CrmProvider,
    ("events", "visibility"): EventVisibility,
    ("organization_members", "role"): OrgRole,
    ("event_staff", "role"): EventStaffRole,
    ("subscriptions", "source"): EntitlementSource,
    ("subscriptions", "status"): EntitlementStatus,
    ("subscriptions", "subject_kind"): SubjectKind,
    ("entitlements", "source"): EntitlementSource,
    ("entitlements", "status"): EntitlementStatus,
    ("entitlements", "subject_kind"): SubjectKind,
    ("billing_events", "source"): EntitlementSource,
    ("reports", "status"): ReportStatus,
}


async def _allowed(conn: AsyncConnection, table: str, column: str) -> set[str]:
    """Parse the permitted values out of the CHECK constraint definition."""
    definition = await conn.scalar(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
        {"name": f"{table}_{column}_check_values"},
    )
    if definition is None:
        return set()
    return set(re.findall(r"'([^']+)'", definition))


@pytest.mark.parametrize(("table", "column"), sorted(ENUMS))
async def test_values_match_database(conn: AsyncConnection, table: str, column: str) -> None:
    python_values = {m.value for m in ENUMS[(table, column)]}
    db_values = await _allowed(conn, table, column)

    assert db_values, (
        f"no CHECK constraint {table}_{column}_check_values. Either it was "
        "never written or it was renamed, and the column now accepts anything."
    )

    missing_in_python = db_values - python_values
    assert not missing_in_python, (
        f"{table}.{column}: database allows {sorted(missing_in_python)} with no "
        "Python member. Reading such a row raises LookupError at runtime."
    )

    missing_in_db = python_values - db_values
    assert not missing_in_db, (
        f"{table}.{column}: Python has {sorted(missing_in_db)} which the "
        "constraint rejects. Writing it fails with a constraint violation."
    )


async def test_every_constraint_has_a_python_mirror(conn: AsyncConnection) -> None:
    """Catches a constrained column added to the schema and nowhere else."""
    rows = await conn.execute(
        text(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint"
            " WHERE conname LIKE '%\\_check\\_values'"
        )
    )
    found = {
        (table, name.removeprefix(f"{table}_").removesuffix("_check_values"))
        for table, name in rows.all()
    }
    unmirrored = found - set(ENUMS)
    assert not unmirrored, (
        f"constrained columns with no Python mirror: {sorted(unmirrored)}. "
        "Add a StrEnum and register it in ENUMS above."
    )


async def test_no_native_enum_types_remain(conn: AsyncConnection) -> None:
    """ADR-0027 moved off Postgres enums. Adding one back reintroduces the
    problem: a value can never be removed, and ADD VALUE cannot run inside a
    transaction."""
    count = await conn.scalar(
        text(
            "SELECT count(*) FROM pg_type t"
            " JOIN pg_enum e ON e.enumtypid = t.oid"
            " JOIN pg_namespace n ON n.oid = t.typnamespace"
            " WHERE n.nspname = 'public'"
        )
    )
    assert count == 0, (
        "a native Postgres enum type exists. Use text + a CHECK constraint "
        "named <table>_<column>_check_values (ADR-0027)."
    )
