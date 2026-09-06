"""Alembic environment.

Runs SYNCHRONOUSLY on psycopg, while the application runs on asyncpg.

That is deliberate, not an oversight. asyncpg sends every statement as a
prepared statement, and PostgreSQL refuses multiple commands in one:

    asyncpg.exceptions.PostgresSyntaxError:
        cannot insert multiple commands into a prepared statement

A baseline migration containing 600 lines of DDL therefore cannot run on
asyncpg at all. Alembic has no reason to be async, so it uses psycopg and the
URL is rewritten below.

Two other things here are load-bearing rather than boilerplate:

  lock_timeout        A migration that would block fails fast instead of
                      queueing every request behind it. Without this, one
                      unlucky migration takes the site down and presents as a
                      hang (schema/migration-policy.md).

  autocommit_block    CREATE INDEX CONCURRENTLY cannot run inside a
                      transaction. Every index migration on a live table needs
                      the block, or it takes an ACCESS EXCLUSIVE lock.
"""

from logging.config import fileConfig

from alembic import context
from pydantic import PostgresDsn, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection

from acme import registry  # noqa: F401  (registers every model)
from acme.core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


class MigrationSettings(BaseSettings):
    """Just the database URL.

    Reads DATABASE_URL from the environment, falling back to .env, so
    `uv run alembic upgrade head` works with no exports.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn


def _sync_url(url: str) -> str:
    """asyncpg -> psycopg. See the module docstring for why."""
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


def _resolve_url() -> str:
    try:
        settings = MigrationSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise SystemExit(
            "DATABASE_URL is not set.\n\n"
            "Set it in the environment or in apps/api/.env:\n"
            "  DATABASE_URL=postgresql+asyncpg://postgres:postgres"
            "@localhost:5432/acme\n\n"
            "It is deliberately absent from alembic.ini: a URL there would "
            "either commit credentials or quietly point migrations at the "
            "wrong database."
        ) from exc

    return _sync_url(str(settings.database_url))


config.set_main_option("sqlalchemy.url", _resolve_url())

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    connection.execute(text("SET lock_timeout = '5s'"))
    connection.execute(text("SET statement_timeout = '300s'"))

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _configure(connection)
        # SQLAlchemy 2.0 does not autocommit. Without this the DDL runs, the
        # migration reports success, and everything is rolled back when the
        # connection closes - leaving a clean database and a green log.
        connection.commit()

    connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
