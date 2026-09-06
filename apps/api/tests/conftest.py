"""Test fixtures.

Tests run against a REAL PostgreSQL instance, never SQLite. The schema depends
on partial indexes, citext, JSONB operators, triggers and LISTEN/NOTIFY, none of
which SQLite has. Testing against SQLite would mean the invariants that matter
most are the ones never exercised.

CI provides Postgres as a service container; locally it comes from
infra/compose/docker-compose.dev.yml.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/acme_test",
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Function-scoped, with NullPool.

    NOT session-scoped. pytest-asyncio creates a fresh event loop per test, and
    an engine created on one loop cannot be used on another: asyncpg raises
    "attached to a different loop" and then "Event loop is closed" during
    teardown. Session scope would need matching loop_scope on every fixture and
    test, which is more configuration surface than it saves.

    NullPool because a pooled connection outliving its loop is the same bug.
    """
    eng = create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def conn(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside a transaction that is always rolled back.

    Every test therefore starts from the same state and none of them can leak
    rows into another. Rolled back rather than truncated because truncation on
    a schema with this many foreign keys is slow and easy to get wrong.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
def uid() -> "UuidFactory":
    return UuidFactory()


class UuidFactory:
    """Deterministic, readable UUIDv7-shaped ids.

    Real ids come from acme.core.ids.new_id(). These exist so a failing test
    prints something a human can match up across tables.
    """

    def __init__(self) -> None:
        self._n = 0

    def __call__(self, label: str = "") -> str:
        self._n += 1
        return f"01900000-0000-7000-8000-{self._n:012d}"
