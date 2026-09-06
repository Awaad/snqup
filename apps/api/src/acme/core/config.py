"""Application settings.

Every value comes from the environment. Nothing is hardcoded, and no secret
has a default (ADR-0013) - a missing secret must fail loudly at startup rather
than silently falling back to something that works in dev.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    environment: Literal["local", "staging", "production"] = "local"
    version: str = "dev"
    log_level: str = "INFO"

    database_url: PostgresDsn
    # LISTEN needs a connection outside the pooler. Supavisor in transaction
    # mode silently breaks the SSE listener (ADR-0006). This is a separate
    # direct connection for that reason and must not be pointed at the pooler.
    database_listen_url: PostgresDsn

    redis_url: RedisDsn

    jwt_issuer: str
    jwt_audience: str
    # Rotation is two-phase: new key is accepted here before it signs anything
    # (runbooks/secret-rotation.md). A single-key field would force an outage.
    jwt_public_keys: list[str] = Field(default_factory=list)

    sentry_dsn: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


class DatabaseSettings(BaseSettings):
    """Just the database URL, resolved from the environment or .env.

    Deliberately narrow. Alembic and the test suite both need to find the
    database and neither needs a JWT issuer, an audience or Redis. Requiring
    the full Settings for a migration is what leads to someone pasting a URL
    into alembic.ini; requiring it for tests leads to a hardcoded fallback in
    conftest.

    There is no default. A missing URL must say so, not silently point at a
    database that does not exist.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: PostgresDsn
    #: Optional override so the suite can target a database other than the one
    #: used for development. Unset is fine: every fixture rolls back, so
    #: running against the dev database leaves nothing behind.
    test_database_url: PostgresDsn | None = None

    @property
    def testing_url(self) -> str:
        return str(self.test_database_url or self.database_url)


@lru_cache
def get_settings() -> Settings:
    # Values come from the environment; mypy cannot see that.
    return Settings.model_validate({})
