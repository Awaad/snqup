"""Connections request and response schemas.

The split between what the edge carries and what a view carries is the whole
point of ADR-0003, and it shows up here: `note` and `tags` are on the view
schemas, never on the connection schema, because they belong to one participant
and the other must never receive them.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from acme.domains.connections.enums import ConnectionState, ScanChannel


class CounterpartCard(BaseModel):
    """The other person, as they were AT THE TIME (ADR-0004).

    Rendered from the snapshot, not from their live card. The client shows the
    live version with a "details changed since you met" affordance and reveals
    this on request.
    """

    model_config = ConfigDict(extra="allow")

    display_name: str | None = None
    headline: str | None = None
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    photo_path: str | None = None
    socials: dict[str, str] = Field(default_factory=dict)


class ConnectionOut(BaseModel):
    """One row in the connection list.

    Carries the caller's OWN note and tags. The counterpart's are never
    included and are not reachable from here - they live in a different row
    with a different owner.
    """

    id: UUID
    connection_id: UUID
    state: ConnectionState
    channel: ScanChannel
    occurred_at: datetime
    event_id: UUID | None
    counterpart: CounterpartCard
    counterpart_changed: bool = Field(
        default=False,
        description=(
            "True when the counterpart's live card differs from the snapshot "
            "taken at exchange time."
        ),
    )
    note: str | None
    note_conflict: str | None = Field(
        default=None,
        description=(
            "Both texts, preserved when two devices edited the note offline. "
            "Never silently overwritten (ADR-0016)."
        ),
    )
    tags: list[str]
    reminder_at: datetime | None
    reminder_done_at: datetime | None
    archived_at: datetime | None
    merged_into_id: UUID | None
    created_at: datetime


class ConnectionListOut(BaseModel):
    """Cursor-paginated. Never offset.

    Offset pagination on a growing table skips and duplicates rows as new ones
    are inserted, and connections only grow (contracts/api-conventions.md).
    """

    items: list[ConnectionOut]
    next_cursor: str | None = None


class ConnectionUpdate(BaseModel):
    """Every field optional. Absent means leave alone; null means clear."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    reminder_at: datetime | None = None
    archived: bool | None = None
    #: Accept the merged note after an offline conflict, clearing the marker.
    resolve_note_conflict: bool = False


class MergeRequest(BaseModel):
    """Fold one connection view into another.

    Meeting the same person at three events is one contact with three event
    tags, not three contacts. The EDGES stay - each records a real meeting -
    and only the caller's own views are merged.
    """

    model_config = ConfigDict(extra="forbid")

    source_ids: list[UUID] = Field(min_length=1, max_length=20)


class ReminderOut(BaseModel):
    connection_id: UUID
    counterpart_name: str | None
    reminder_at: datetime
