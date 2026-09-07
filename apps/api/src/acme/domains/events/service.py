"""Events service."""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.billing.service import EntitlementsService, Subject
from acme.domains.connections.service import EventConnectionStats
from acme.domains.events.enums import EventStaffRole, EventVisibility
from acme.domains.events.models import (
    Event,
    EventAttendee,
    EventContent,
    EventRosterEntry,
    EventStaff,
)
from acme.domains.events.schemas import EventCreate, EventStatsOut
from acme.domains.identity.service import IdentityService


class EventsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def window_for_attendee(self, event_id: UUID, user_id: UUID) -> tuple[datetime, datetime]:
        """The event's time window, if this user is actually an attendee.

        Membership is checked here rather than trusted from the request. A
        client that could tag any exchange with any event id could inflate
        another organizer's numbers, and those numbers are what the organizer
        pays for.
        """
        stmt = (
            select(Event)
            .join(
                EventAttendee,
                (EventAttendee.event_id == Event.id)
                & (EventAttendee.user_id == user_id)
                & (EventAttendee.deleted_at.is_(None)),
            )
            .where(Event.id == event_id, Event.deleted_at.is_(None))
        )
        event = (await self._session.execute(stmt)).scalar_one_or_none()
        if event is None:
            # Same code whether the event does not exist or the user is not an
            # attendee: distinguishing them confirms an event exists to someone
            # with no access to it.
            raise ApiError("EVENT_NOT_FOUND", status_code=404)
        return event.starts_at, event.ends_at

    async def content_for(self, event_id: UUID) -> EventContent | None:
        """Presentational content, read through the service (ADR-0027).

        Never joined from a router. Storage is an implementation detail, so a
        CMS or structured session tables become a change here and nowhere else.
        """
        return await self._session.get(EventContent, event_id)


# Below this cohort size, aggregates identify individuals: "2 of 3 connected"
# names people. Every organizer-facing number is suppressed under it (ADR-0012).
MIN_COHORT = 10


class EventDigestService:
    """Finding events whose post-event digest is due.

    Due-ness is computed in the event's LOCAL time, which is why the IANA
    timezone is validated on create - an invalid name would silently skip the
    single best retention mechanic in the product.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def due_for_digest(self, at: datetime) -> list[tuple[Event, list[UUID]]]:
        events = (
            await self._session.execute(
                select(Event)
                .where(Event.deleted_at.is_(None))
                .where(Event.ends_at <= at)
                # A short window: this runs often, and an event that ended a
                # month ago is not suddenly due.
                .where(Event.ends_at >= at - timedelta(days=3))
            )
        ).scalars()

        due: list[tuple[Event, list[UUID]]] = []
        for event in events:
            local_end = event.ends_at.astimezone(ZoneInfo(event.timezone))
            if at < (local_end + timedelta(days=1)).astimezone(UTC):
                continue
            attendees = list(
                (
                    await self._session.execute(
                        select(EventAttendee.user_id).where(
                            EventAttendee.event_id == event.id,
                            EventAttendee.deleted_at.is_(None),
                        )
                    )
                ).scalars()
            )
            due.append((event, attendees))
        return due


class EventAdminService:
    """Event management for organizers.

    Separate from EventsService, which the exchange uses for membership lookups
    during a scan. This one is always acting for a specific organizer and
    checks their authority to do so.
    """

    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self._session = session
        self._user_id = user_id
        self._entitlements = EntitlementsService(session)

    async def _assert_staff(
        self, event_id: UUID, *, roles: set[EventStaffRole] | None = None
    ) -> Event:
        """Event staff, NOT organization membership (ADR-0018).

        A scanner hired for one day must see one event and nothing else. "Can
        edit the organization's brand" and "can view this event's dashboard"
        are different powers, and neither implies the other.
        """
        stmt = (
            select(Event)
            .join(
                EventStaff,
                (EventStaff.event_id == Event.id)
                & (EventStaff.user_id == self._user_id)
                & (EventStaff.deleted_at.is_(None)),
            )
            .where(Event.id == event_id, Event.deleted_at.is_(None))
        )
        if roles is not None:
            stmt = stmt.where(EventStaff.role.in_(list(roles)))

        event = (await self._session.execute(stmt)).scalar_one_or_none()
        if event is None:
            # Same code whether the event does not exist or this user has no
            # role on it: distinguishing them confirms an event exists to
            # someone with no access.
            raise ApiError("EVENT_NOT_FOUND", status_code=404)
        return event

    async def create(self, organization_id: UUID, payload: EventCreate) -> Event:
        if payload.ends_at <= payload.starts_at:
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message="ends_at must be after starts_at",
            )
        try:
            ZoneInfo(payload.timezone)
        except (KeyError, ValueError) as exc:
            # An invalid IANA name silently breaks the post-event digest, which
            # fires 24h after ends_at in LOCAL time, and every activity chart.
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message=f"unknown IANA timezone {payload.timezone!r}",
            ) from exc

        if payload.visibility == EventVisibility.PUBLIC:
            await self._assert_public_listing_allowed(organization_id)

        event = Event(
            organization_id=organization_id,
            created_by=self._user_id,
            name=payload.name,
            venue=payload.venue,
            code=_new_join_code(),
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            timezone=payload.timezone,
            visibility=payload.visibility,
            leaderboard_enabled=payload.leaderboard_enabled,
        )
        self._session.add(event)
        await self._session.flush()

        # The creator is owner staff. Without this the person who made the
        # event cannot open its dashboard.
        self._session.add(
            EventStaff(
                event_id=event.id,
                user_id=self._user_id,
                role=EventStaffRole.OWNER,
            )
        )
        await self._session.flush()
        return event

    async def _assert_public_listing_allowed(self, organization_id: UUID) -> None:
        """Public indexed pages require a verified domain or a paid plan.

        That gate is both the SEO strategy and the anti-spam filter: nobody
        pays to host a phishing page, and an ungated public listing on our
        domain is how a Safe Browsing blocklisting starts (ADR-0008).
        """
        if await IdentityService(self._session).has_verified_domain(organization_id):
            return
        if await self._entitlements.allowed(
            Subject.organization(organization_id), "event.branded_page"
        ):
            return
        raise ApiError(
            "ORG_DOMAIN_NOT_VERIFIED",
            status_code=403,
            message="public events need a verified domain or a paid plan",
        )

    async def join(self, code: str, user_id: UUID, card_id: UUID | None) -> Event:
        event = (
            await self._session.execute(
                select(Event).where(Event.code == code, Event.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if event is None:
            raise ApiError("EVENT_CODE_INVALID", status_code=404)

        existing = (
            await self._session.execute(
                select(EventAttendee).where(
                    EventAttendee.event_id == event.id,
                    EventAttendee.user_id == user_id,
                    EventAttendee.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Rejoining is not an error. A user who taps join twice, or who
            # left and came back, gets the same result.
            existing.card_id = card_id or existing.card_id
            return event

        limit = await self._entitlements.limit(
            Subject.organization(event.organization_id), "event.attendee_limit"
        )
        if limit != -1 and await self._attendee_count(event.id) >= limit:
            # Hitting this mid-event is a terrible customer moment, which is
            # why runbooks/event-day.md checks capacity the week before.
            raise ApiError(
                "EVENT_ATTENDEE_LIMIT_REACHED",
                status_code=403,
                message=f"plan allows {limit} attendees",
                details={"limit": limit},
            )

        self._session.add(EventAttendee(event_id=event.id, user_id=user_id, card_id=card_id))
        await self._session.flush()
        return event

    async def _attendee_count(self, event_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(EventAttendee)
            .where(
                EventAttendee.event_id == event_id,
                EventAttendee.deleted_at.is_(None),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def import_roster(self, event_id: UUID, entries: list[tuple[str, str | None]]) -> int:
        """Upload the organizer's registration list.

        Gives the dashboard a denominator, which is what turns "400
        connections" into "78% of your attendees connected" - the number
        organizers are actually asked about.

        Idempotent on (event_id, email): organizers re-upload lists, and a
        silent duplicate would double the denominator and halve the headline
        number.
        """
        event = await self._assert_staff(
            event_id, roles={EventStaffRole.OWNER, EventStaffRole.MANAGER}
        )
        if not await self._entitlements.allowed(
            Subject.organization(event.organization_id), "event.attendee_import"
        ):
            raise ApiError(
                "BILLING_ENTITLEMENT_MISSING",
                status_code=403,
                details={"entitlement": "event.attendee_import"},
            )

        existing = {
            row.email
            for row in (
                await self._session.execute(
                    select(EventRosterEntry).where(EventRosterEntry.event_id == event_id)
                )
            ).scalars()
        }
        added = 0
        for email, name in entries:
            normalised = email.strip().lower()
            if not normalised or normalised in existing:
                continue
            self._session.add(
                EventRosterEntry(event_id=event_id, email=normalised, display_name=name)
            )
            existing.add(normalised)
            added += 1
        await self._session.flush()
        return added

    async def stats(self, event_id: UUID) -> EventStatsOut:
        """AGGREGATES ONLY (ADR-0012).

        Nothing here reveals which attendee connected with which. That is
        third-party disclosure of relationship data neither party consented to,
        and there is no lawful basis for it.

        Suppressed below MIN_COHORT, because on a small event an aggregate
        identifies individuals however it is phrased.
        """
        await self._assert_staff(event_id)

        attendees = await self._attendee_count(event_id)
        connections, unique_count, anonymous = await EventConnectionStats(self._session).for_event(
            event_id
        )

        if attendees < MIN_COHORT:
            return EventStatsOut(
                attendees=attendees,
                connections=0,
                unique_connectors=0,
                connected_percentage=None,
                anonymous_scans=0,
                suppressed=True,
            )

        return EventStatsOut(
            attendees=attendees,
            connections=connections,
            unique_connectors=unique_count,
            connected_percentage=round(100 * unique_count / attendees, 1),
            anonymous_scans=anonymous,
        )


def _new_join_code() -> str:
    """Short, human-readable, unambiguous.

    Read aloud and typed at a venue, so 0/O and 1/I/L are excluded - a code
    that gets mistyped at the door is worse than a longer one.
    """
    import secrets

    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))
