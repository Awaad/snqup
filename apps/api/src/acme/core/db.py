"""Database access.

Two engines on purpose (ADR-0006):

  engine         pooled, for normal request work
  listen_engine  a DIRECT connection for Postgres LISTEN

LISTEN cannot run through a transaction-mode pooler. Supavisor in transaction
mode will accept the connection and then silently never deliver a notification,
which presents as the organizer dashboard connecting fine and never ticking.
That is a bad failure to debug during someone's conference.
"""

from collections.abc import AsyncIterator
from enum import Enum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import types
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from acme.core.config import Settings


class CIText(types.UserDefinedType[str]):
    """Postgres citext.

    Case-insensitive comparison in the database rather than lower() on every
    query, which would also defeat the index.
    """

    cache_ok = True

    def get_col_spec(self, **_kw: object) -> str:
        return "CITEXT"


def constrained[E: Enum](python_enum: type[E]) -> SAEnum:
    """Map a Python enum onto a `text` column with a CHECK constraint.

    native_enum=False so SQLAlchemy treats the column as text rather than
    expecting a Postgres enum type. create_constraint=False because the
    migration owns the schema - the CHECK already exists and model metadata
    must not try to add a second one.

    length=None keeps it TEXT rather than VARCHAR(n). SQLAlchemy would
    otherwise size the column to the longest CURRENT value, so adding a longer
    value later would need an ALTER COLUMN TYPE - the exact friction we moved
    off Postgres enums to avoid.

    values_callable makes the VALUES travel, not the member NAMES. Without it
    SQLAlchemy sends `PERSONAL` where the constraint expects `personal`.

    tests/test_enum_sync.py compares these against the database CHECK
    constraints in both directions.
    """
    return SAEnum(
        python_enum,
        native_enum=False,
        create_constraint=False,
        length=None,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    """Declarative base for every model.

    Models live in their own domain package. Nothing outside a domain may
    import them (ADR-0025).

    Two rules that keep that boundary real:

    1. Cross-domain foreign keys are declared by TABLE NAME STRING
       (ForeignKey("users.id")), never by importing the other domain's class.
       A string carries no import.

    2. No ORM relationship() crosses a domain boundary, because it would need
       that import. Cross-domain data comes from a service call. This costs
       some convenience and is the entire reason the boundary holds.
    """


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.database_url),
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        # Async SQLAlchemy opens more connections than people expect. Recycling
        # keeps us inside Supavisor's limits, which is the failure that presents
        # as /health passing while /health/ready times out.
        pool_recycle=1800,
        echo=False,
    )


def create_listen_engine(settings: Settings) -> AsyncEngine:
    """Direct connection, NullPool. Must not point at the pooler."""
    from sqlalchemy.pool import NullPool

    return create_async_engine(
        str(settings.database_listen_url),
        poolclass=NullPool,
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(request: Any) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request, committed on success."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
