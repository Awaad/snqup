"""Event endpoints.

Organizer surfaces. Note what is NOT here: no billing. Organizer and
organization plans are sold on the web only, and the mobile app contains no
upsell for them - an App Store compliance requirement, not a preference
(ADR-0009).
"""

from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from acme.api.deps import CurrentUserDep, RateLimiterDep, SessionDep
from acme.domains.events.schemas import (
    EventCreate,
    EventOut,
    EventRegistration,
    EventStatsOut,
    PublicEventOut,
    RegistrationResult,
)
from acme.domains.events.service import EventAdminService, PublicEventService

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


# -- public (api/public_routes.py) ----------------------------------------

public_router = APIRouter(prefix="/v1", tags=["public"])

#: Per IP. Self-registration is an unauthenticated write from a stranger, so
#: it is the obvious spam target - but a venue is hundreds of people behind one
#: NAT, so the limit is loose and FAILS OPEN (ADR-0007).
REGISTER_LIMIT_PER_IP = 10
REGISTER_WINDOW_SECONDS = 3600


@public_router.get("/events/public/{slug}", response_model=PublicEventOut)
async def public_event(slug: str, session: SessionDep) -> PublicEventOut:
    """The public event page. NO AUTHENTICATION.

    This surface did not exist, which was a gap rather than a decision:
    `events.slug` was in the schema with a unique index and nothing used it, so
    an organizer putting "register at example.net/e/devcon" on a slide had
    nowhere for that link to land.

    PRIVATE events never resolve here. Unlisted ones do, with `indexable:
    false` so the page renders noindex - the difference between "anyone with
    the link" and "anyone at all" (ADR-0008).
    """
    return await _render_public_event(session, await PublicEventService(session).by_slug(slug))


@public_router.get("/events/code/{code}", response_model=PublicEventOut)
async def public_event_by_code(code: str, session: SessionDep) -> PublicEventOut:
    """Resolve a join code from a QR on a badge, a slide or a programme.

    Codes resolve regardless of visibility: holding one IS the invitation,
    which is the whole reason a private event has a code.
    """
    return await _render_public_event(session, await PublicEventService(session).by_code(code))


@public_router.post("/events/public/{slug}/register", response_model=RegistrationResult)
async def register_for_event(
    slug: str,
    payload: EventRegistration,
    request: Request,
    session: SessionDep,
    limiter: RateLimiterDep,
) -> RegistrationResult:
    """Register for an event without an account.

    The growth loop applied to events: someone scans an organizer's QR, lands
    here, and is on the roster before they have installed anything. Requiring
    a signup first is the friction that loses the majority who do not have the
    app.

    Returns the join code so a visitor who DOES have the app can deep-link
    straight into it rather than typing the code by hand.
    """
    if payload.website:
        # Honeypot filled: a bot. Return success so it learns nothing about
        # which field gave it away.
        return RegistrationResult(registered=True)

    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    await limiter.check(
        f"event:register:{ip}",
        limit=REGISTER_LIMIT_PER_IP,
        window_seconds=REGISTER_WINDOW_SECONDS,
    )

    service = PublicEventService(session)
    event = await service.by_slug(slug)
    created = await service.register_anonymously(
        event, email=str(payload.email), display_name=payload.display_name
    )
    return RegistrationResult(
        registered=True,
        already_registered=not created,
        join_code=event.code,
    )


async def _render_public_event(session: SessionDep, event: object) -> PublicEventOut:
    """Assemble the page, including organizer branding and content.

    Content comes through EventsService.content_for() rather than a join, so a
    future CMS is a resolver change (ADR-0027).
    """
    from acme.domains.events.enums import EventVisibility
    from acme.domains.events.service import EventsService
    from acme.domains.identity.service import IdentityService

    content = await EventsService(session).content_for(event.id)  # type: ignore[attr-defined]
    org = await IdentityService(session).organization_branding(
        event.organization_id  # type: ignore[attr-defined]
    )

    return PublicEventOut(
        name=event.name,  # type: ignore[attr-defined]
        venue=event.venue,  # type: ignore[attr-defined]
        starts_at=event.starts_at,  # type: ignore[attr-defined]
        ends_at=event.ends_at,  # type: ignore[attr-defined]
        timezone=event.timezone,  # type: ignore[attr-defined]
        organization_name=org.get("name") if org else None,
        organization_logo_path=org.get("logo_path") if org else None,
        body=content.body if content else None,
        banner_path=content.banner_path if content else None,
        # == not `is`. These are StrEnum members, and the ORM hands back a raw
        # string before a refresh - so identity comparison silently returns
        # False for a value that is correct. It failed for exactly that reason
        # in a test, on a public event that reported itself as non-indexable.
        indexable=event.visibility == EventVisibility.PUBLIC,  # type: ignore[attr-defined]
    )
