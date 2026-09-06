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


@lru_cache
def get_settings() -> Settings:
    # Values come from the environment; mypy cannot see that.
    return Settings.model_validate({})
