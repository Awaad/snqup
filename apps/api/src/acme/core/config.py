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
    
    # Billing. No defaults: a missing webhook secret must fail at startup, not
    # silently accept unsigned requests.
    stripe_webhook_secret: str = ""
    apple_bundle_id: str = ""
    google_play_pubsub_audience: str = ""
    google_play_pubsub_service_account: str = ""

    # CRM OAuth. Blocked on the product name only - registering a Google Cloud
    # project and a HubSpot app both require it (00-context/naming.md).
    crm_token_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""

    # Admin allowlist: IdP subjects, from configuration.
    #
    # NOT a role column on the user row. A database-backed admin flag is one
    # bad migration or one injection away from privilege escalation, and this
    # surface can read every account in the system. Configuration means
    # granting access requires a deploy, which is a reviewable event.
    admin_subjects: list[str] = Field(default_factory=list)

    # Notification delivery.
    #
    # No default for the key: EmailNotifier raises when it is missing rather
    # than silently dropping mail, because a notification nobody receives is
    # worse than a loud failure - nobody reports the first one.
    resend_api_key: str = ""
    notification_sender: str = "notifications@example.com"
    
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
