"""Events request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from acme.domains.events.enums import EventVisibility


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    venue: str | None = Field(default=None, max_length=300)
    starts_at: datetime
    ends_at: datetime
    #: IANA name, e.g. "Europe/Berlin". NOT an offset: peak-activity analytics
    #: are meaningless without it and DST makes offsets wrong twice a year.
    timezone: str
    visibility: EventVisibility = EventVisibility.UNLISTED
    #: Off by default. Gamifying scan counts produces farmed connections.
    leaderboard_enabled: bool = False


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    venue: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str | None = None
    visibility: EventVisibility | None = None
    leaderboard_enabled: bool | None = None


class EventContentUpdate(BaseModel):
    """Presentational content, separate from the event itself (ADR-0027).

    `body` is Tiptap JSON, sanitized on write AND on read. Never raw HTML:
    user HTML on the public domain is the highest-consequence vulnerability in
    this product.
    """

    model_config = ConfigDict(extra="forbid")

    body: dict[str, object] | None = None
    banner_path: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    name: str
    venue: str | None
    code: str
    slug: str | None
    visibility: EventVisibility
    starts_at: datetime
    ends_at: datetime
    timezone: str
    leaderboard_enabled: bool


class EventStatsOut(BaseModel):
    """AGGREGATES ONLY (ADR-0012).

    No field here may reveal which attendee connected with which. That is
    third-party disclosure of relationship data neither party consented to, and
    every aggregate is suppressed below a cohort of 10 because "2 of 3
    connected" identifies people.
    """

    attendees: int
    connections: int
    unique_connectors: int
    #: The number organizers are actually asked about.
    connected_percentage: float | None
    #: Scans by people with no account - the fallback surface, measurable.
    anonymous_scans: int
    suppressed: bool = Field(
        default=False,
        description="True when the cohort is too small to report safely.",
    )
