"""CRM request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from acme.domains.crm.enums import CrmProvider


class CrmConnectionOut(BaseModel):
    """A connected CRM, as the user sees it.

    Credentials are NEVER serialised. `needs_reauth` and `last_error` are the
    whole point of this shape: they are what let the app show "reconnect"
    rather than pretending everything is fine while nothing syncs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: CrmProvider
    last_sync_at: datetime | None
    last_error: str | None
    needs_reauth: bool
    created_at: datetime


class CrmConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: CrmProvider
    #: OAuth authorization code. Exchanged server-side; the client never holds
    #: a long-lived token.
    code: str = Field(min_length=1)
    redirect_uri: str


class FieldMappingUpdate(BaseModel):
    """Which CRM property receives which of our fields.

    Per connection, because custom property names are chosen per portal. An
    unmapped field is dropped rather than guessed at - writing "how we met"
    into whatever property happens to exist is worse than not writing it,
    because nobody notices.
    """

    model_config = ConfigDict(extra="forbid")

    mapping: dict[str, str] = Field(max_length=50)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Omitted means everything not yet synced.
    connection_view_ids: list[UUID] | None = Field(default=None, max_length=500)


class SyncSummary(BaseModel):
    created: int = 0
    updated: int = 0
    #: Already synced. Not a failure - it is the external id doing its job.
    skipped: int = 0
    failed: int = 0
    needs_reauth: bool = False
