"""Event endpoints.

Organizer surfaces. Note what is NOT here: no billing. Organizer and
organization plans are sold on the web only, and the mobile app contains no
upsell for them - an App Store compliance requirement, not a preference
(ADR-0009).
"""

from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.events.schemas import EventCreate, EventOut, EventStatsOut
from acme.domains.events.service import EventAdminService

router = APIRouter(prefix="/v1", tags=["events"])


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=4, max_length=16)
    #: Which card to present at this event. Optional: an attendee who has not
    #: chosen falls back to their default.
    card_id: UUID | None = None


class RosterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str | None = Field(default=None, max_length=120)


class RosterImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[RosterEntry] = Field(min_length=1, max_length=5000)


class RosterImportResult(BaseModel):
    added: int
    #: Re-uploading a list is normal, so duplicates are skipped rather than
    #: rejected - and reported, so the organizer can see it worked.
    skipped: int


@router.post(
    "/organizations/{organization_id}/events",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    organization_id: UUID,
    payload: EventCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> EventOut:
    """Create an event.

    The creator becomes owner staff, without which the person who made the
    event could not open its dashboard.
    """
    event = await EventAdminService(session, user.id).create(organization_id, payload)
    return EventOut.model_validate(event)


@router.post("/events/join", response_model=EventOut)
async def join_event(payload: JoinRequest, session: SessionDep, user: CurrentUserDep) -> EventOut:
    """Join by code.

    Rejoining is not an error: someone who taps twice, or who left and came
    back, gets the same result.
    """
    event = await EventAdminService(session, user.id).join(payload.code, user.id, payload.card_id)
    return EventOut.model_validate(event)


@router.post("/events/{event_id}/roster", response_model=RosterImportResult)
async def import_roster(
    event_id: UUID,
    payload: RosterImport,
    session: SessionDep,
    user: CurrentUserDep,
) -> RosterImportResult:
    """Upload the registration list.

    Gives the dashboard a denominator, which is what turns "400 connections"
    into "78% of your attendees connected" - the number organizers are actually
    asked about.
    """
    added = await EventAdminService(session, user.id).import_roster(
        event_id, [(e.email, e.name) for e in payload.entries]
    )
    return RosterImportResult(added=added, skipped=len(payload.entries) - added)


@router.get("/events/{event_id}/stats", response_model=EventStatsOut)
async def event_stats(event_id: UUID, session: SessionDep, user: CurrentUserDep) -> EventStatsOut:
    """AGGREGATES ONLY (ADR-0012).

    No field here reveals which attendee connected with which - that is
    third-party disclosure of relationship data neither party consented to.
    Suppressed entirely below a cohort of 10, because on a small event any
    aggregate identifies individuals however it is phrased.
    """
    return await EventAdminService(session, user.id).stats(event_id)
