"""Identifier generation (ADR-0010).

Three distinct concepts that are deliberately not interchangeable. Getting one
wrong is silent: a v4 primary key still works, it just degrades index locality
over months, and a UUID public token still resolves, it just leaks a timestamp
and makes the QR denser.
"""

import re
from uuid import UUID

from acme.core.ids import new_id, new_token


def test_ids_are_uuid_v7() -> None:
    """v7, not v4.

    v7 is time-ordered, so inserts on an append-only table like `connections`
    stay sequential instead of scattering across the B-tree and causing
    constant page splits.
    """
    for _ in range(20):
        assert new_id().version == 7


def test_ids_are_monotonic() -> None:
    """The property that makes v7 worth having.

    If this fails, the ordering guarantee is gone and v7 is just a slower v4.
    """
    ids = [new_id() for _ in range(200)]
    assert ids == sorted(ids)


def test_models_generate_ids_without_being_asked() -> None:
    """Every UUID primary key carries default=new_id.

    Without it, an insert that forgets to pass an id fails at the database
    rather than being handled, and nothing points at the fix. Client-supplied
    ids (offline creation, ADR-0016) still win, because a default only applies
    when no value was given.
    """
    from acme import registry  # noqa: F401
    from acme.core.db import Base

    missing: list[str] = []
    for table in Base.metadata.tables.values():
        for column in table.primary_key.columns:
            if not isinstance(column.type, type(Base.metadata.tables["users"].c.id.type)):
                continue
            # Composite keys and FK-based keys are supplied by the caller.
            if column.foreign_keys or len(table.primary_key.columns) > 1:
                continue
            if column.default is None:
                missing.append(f"{table.name}.{column.name}")

    assert not missing, (
        f"UUID primary keys with no default: {missing}. Add default=new_id (ADR-0010)."
    )


def test_public_tokens_are_not_uuids() -> None:
    """Tokens are opaque, ~22 chars, URL-safe.

    NOT a UUID: a v7 would leak its creation time to anyone holding a badge
    photo, and 36 characters produces a denser, less reliably scannable QR
    code. Density matters more than it sounds when someone is scanning across
    a table in bad light.
    """
    token = new_token()
    assert len(token) == 22
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)

    try:
        UUID(token)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("public token must not parse as a UUID")


def test_tokens_are_unique() -> None:
    assert len({new_token() for _ in range(1000)}) == 1000
