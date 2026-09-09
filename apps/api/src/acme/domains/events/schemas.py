"""Events request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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


class PublicEventOut(BaseModel):
    """What a STRANGER sees on an event page.

    Deliberately narrower than EventOut. No organization id, no join code, no
    attendee counts - a code is an invitation and printing it on a public page
    would make every private event joinable by anyone who found the URL.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    venue: str | None
    starts_at: datetime
    ends_at: datetime
    timezone: str
    #: Organizer branding, so the page looks like theirs rather than ours.
    organization_name: str | None = None
    organization_logo_path: str | None = None
    #: Tiptap JSON, sanitized on write AND on read. Never raw HTML.
    body: dict[str, object] | None = None
    banner_path: str | None = None
    #: False for unlisted events, so the page can render noindex.
    indexable: bool = False


class EventRegistration(BaseModel):
    """Self-registration. No account required.

    Optional by design: most people who see an event link do not have the app,
    and requiring a signup first is the friction that loses them.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    display_name: str | None = Field(default=None, max_length=120)
    #: Honeypot. A real browser leaves it empty; a bot fills every field.
    website: str = ""


class RegistrationResult(BaseModel):
    registered: bool
    #: True when the email was already on the roster. Not an error - someone
    #: who taps twice, or who was on the organizer's upload already, should
    #: see success either way.
    already_registered: bool = False
    #: Deep link into the app, so someone who has it lands in the right place.
    join_code: str | None = None
