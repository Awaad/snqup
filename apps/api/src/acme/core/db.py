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


class ConstrainedText[E: Enum](types.TypeDecorator[E]):
    """A `text` column whose values are a Python enum.

    NOT sqlalchemy.Enum(native_enum=False). That renders as VARCHAR, and
    against a TEXT column `alembic revision --autogenerate` reports a type
    change for EVERY constrained column - eighteen spurious alter_column
    entries per run. People then learn to skim autogenerate output, and a real
    change hides in the noise. That is the exact failure the model/database
    drift test exists to prevent, so the models must not create it.

    impl is Text, so the model type matches the database type exactly and
    autogenerate sees nothing. The enum conversion happens in Python, and the
    permitted set is enforced by a CHECK constraint the migration owns
    (ADR-0027).
    """

    impl = types.Text
    cache_ok = True

    def __init__(self, python_enum: type[E]) -> None:
        self.python_enum = python_enum
        super().__init__()

    def process_bind_param(self, value: E | str | None, dialect: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.python_enum):
            return str(value.value)
        # Accept a raw string, but only one the enum actually permits, so a
        # typo fails here rather than at the CHECK constraint.
        return str(self.python_enum(value).value)

    def process_result_value(self, value: str | None, dialect: object) -> E | None:
        if value is None:
            return None
        return self.python_enum(value)


def constrained[E: Enum](python_enum: type[E]) -> ConstrainedText[E]:
    """Map a Python enum onto a `text` column with a CHECK constraint.

    The StrEnum is now the ONLY place in code where the permitted set is
    written down: the database has no named type carrying them, just a list of
    literals inside a constraint. tests/test_enum_sync.py compares the two in
    both directions.
    """
    return ConstrainedText(python_enum)


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
