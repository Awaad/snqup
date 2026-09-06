"""Test fixtures.

Tests run against a REAL PostgreSQL instance, never SQLite. The schema depends
on partial indexes, citext, JSONB operators, triggers and LISTEN/NOTIFY, none of
which SQLite has. Testing against SQLite would mean the invariants that matter
most are the ones never exercised.

CI provides Postgres as a service container; locally it comes from
infra/compose/docker-compose.dev.yml.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from acme.core.config import DatabaseSettings


def _database_url() -> str:
    """Resolve the same way Alembic does: environment, then .env.

    There is deliberately NO fallback. A hardcoded default here previously sent
    the whole suite at a database called `acme_test` that nothing creates, so
    `pnpm api:migrate` migrated the database named in .env and then every
    database-backed test errored with InvalidCatalogNameError - which reads
    like a broken test suite rather than a missing variable.

    Set TEST_DATABASE_URL to target a separate database. Leaving it unset is
    fine: every fixture runs inside a transaction that is rolled back, so the
    suite leaves nothing behind.
    """
    try:
        return DatabaseSettings().testing_url  # type: ignore[call-arg]
    except ValidationError as exc:
        raise pytest.UsageError(
            "DATABASE_URL is not set.\n\n"
            "Set it in the environment or in apps/api/.env:\n"
            "  DATABASE_URL=postgresql+asyncpg://postgres:postgres"
            "@localhost:5432/acme\n\n"
            "Then: docker compose up -d --wait && pnpm api:migrate"
        ) from exc


DATABASE_URL = _database_url()


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


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """An ORM session inside a transaction that is always rolled back.

    Same isolation guarantee as `conn`, at the ORM level. Tests that exercise
    repositories and services want this; tests that assert on raw DDL want
    `conn`.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with factory() as db_session:
            try:
                yield db_session
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
