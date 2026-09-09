"""Events service."""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.billing.service import EntitlementsService, Subject
from acme.domains.connections.service import EventConnectionStats
from acme.domains.events.enums import (
    EventStaffRole,
    EventVisibility,
    RosterSource,
)
from acme.domains.events.models import (
    Event,
    EventAttendee,
    EventContent,
    EventRosterEntry,
    EventStaff,
)
from acme.domains.events.repository import EventRepository
from acme.domains.events.schemas import (
    EventContentUpdate,
    EventCreate,
    EventStatsOut,
    EventUpdate,
    PublicEventOut,
)
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


class PublicEventService:
    """The public event page and self-registration.

    This surface did not exist, which was a real gap: `events.slug` was in the
    schema with a unique index and no endpoint used it. An organizer putting
    "register at example.net/e/devcon" on a slide had nowhere for that link to
    land.

    Anonymous-first, like the scan page and for the same reason: most people
    who see the link do not have the app, and requiring an account before they
    can register is the friction that loses them (ADR-0008).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_slug(self, slug: str) -> Event:
        """Resolve a public or unlisted event.

        PRIVATE events never resolve by slug. Unlisted ones do - the link
        works, the page is noindex - which is the difference between "anyone
        with the link" and "anyone at all" (ADR-0008).

        Goes through EventRepository rather than querying here: the same lookup
        existed in both places, and two copies of a visibility rule is how one
        of them ends up permitting a private event.
        """
        event = await EventRepository(self._session).by_slug_visible(slug)
        if event is None:
            raise ApiError("EVENT_NOT_FOUND", status_code=404)
        return event

    async def by_code(self, code: str) -> Event:
        """Resolve by join code, for a QR on a badge or a slide.

        Codes resolve regardless of visibility: holding one IS the invitation,
        which is the whole reason a private event has a code at all.
        """
        event = await EventRepository(self._session).by_code(code)
        if event is None:
            raise ApiError("EVENT_CODE_INVALID", status_code=404)
        return event

    async def register_anonymously(
        self, event: Event, *, email: str, display_name: str | None
    ) -> bool:
        """Self-registration, no account required.

        Returns False when this email was already on the list, which is not an
        error: someone who taps register twice, or who was already on the
        organizer's upload, should see success either way.

        Recorded as SELF_REGISTERED so the dashboard can tell a real
        registration list from a landing page's signups. Collapsing them would
        let "78% of your attendees connected" quietly overstate a number the
        organizer repeats to sponsors.
        """
        normalised = email.strip().lower()
        existing = (
            await self._session.execute(
                select(EventRosterEntry).where(
                    EventRosterEntry.event_id == event.id,
                    EventRosterEntry.email == normalised,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False

        self._session.add(
            EventRosterEntry(
                event_id=event.id,
                email=normalised,
                display_name=display_name,
                source=RosterSource.SELF_REGISTERED,
            )
        )
        await self._session.flush()
        return True

    async def render(self, event: Event) -> PublicEventOut:
        """Assemble the public page.

        Lives here rather than in the router because it needs the Event MODEL,
        and entry points may not import a domain's models (import contract 4).
        The router asks for a rendered response and never sees the row.

        Content comes through EventsService.content_for() rather than a join,
        so a future CMS is a resolver change (ADR-0027).
        """
        from acme.domains.identity.service import IdentityService

        content = await EventsService(self._session).content_for(event.id)
        org = await IdentityService(self._session).organization_branding(event.organization_id)

        return PublicEventOut(
            name=event.name,
            venue=event.venue,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            timezone=event.timezone,
            organization_name=org.get("name") if org else None,
            organization_logo_path=org.get("logo_path") if org else None,
            body=content.body if content else None,
            banner_path=content.banner_path if content else None,
            indexable=event.visibility == EventVisibility.PUBLIC,
        )

    async def page_by_slug(self, slug: str) -> PublicEventOut:
        return await self.render(await self.by_slug(slug))

    async def page_by_code(self, code: str) -> PublicEventOut:
        return await self.render(await self.by_code(code))

    async def registration_counts(self, event_id: UUID) -> dict[str, int]:
        """Roster size broken down by source.

        Exposed so the dashboard can show the split rather than one number
        that means different things depending on where the rows came from.
        """
        stmt = (
            select(EventRosterEntry.source, func.count())
            .where(EventRosterEntry.event_id == event_id)
            .group_by(EventRosterEntry.source)
        )
        rows = (await self._session.execute(stmt)).all()
        return {str(source): int(count) for source, count in rows}


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
        """Delegates. The same count existed here and in EventRepository, and
        a capacity check that disagrees with the dashboard is worse than
        either number alone."""
        return await EventRepository(self._session).attendee_count(event_id)

    async def update(self, event_id: UUID, payload: "EventUpdate") -> Event:
        """Change an event after creation.

        This did not exist, which made every event immutable: venues move,
        times shift, and an organizer who typed the wrong date had no recourse
        but to create a second event and re-share the code.

        Owner or manager only. A scanner hired for the day must not be able to
        move the event.
        """
        event = await self._assert_staff(
            event_id, roles={EventStaffRole.OWNER, EventStaffRole.MANAGER}
        )
        changes = payload.model_dump(exclude_unset=True)

        starts = changes.get("starts_at", event.starts_at)
        ends = changes.get("ends_at", event.ends_at)
        if ends <= starts:
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message="ends_at must be after starts_at",
            )

        if "timezone" in changes and changes["timezone"] is not None:
            try:
                ZoneInfo(changes["timezone"])
            except (KeyError, ValueError) as exc:
                # An invalid name silently breaks the post-event digest, which
                # fires 24h after ends_at in LOCAL time.
                raise ApiError(
                    "VALIDATION_FAILED",
                    status_code=422,
                    message=f"unknown IANA timezone {changes['timezone']!r}",
                ) from exc

        if changes.get("visibility") == EventVisibility.PUBLIC:
            # Re-checked on every change to public, not only at creation.
            # Otherwise an event created private becomes an indexed public page
            # with no verification at all.
            await self._assert_public_listing_allowed(event.organization_id)

        for key, value in changes.items():
            setattr(event, key, value)
        await self._session.flush()
        return event

    async def set_content(self, event_id: UUID, payload: "EventContentUpdate") -> EventContent:
        """Write the presentational half.

        `event_content` was split from `events` in ADR-0027 and nothing could
        write it, so a public event page could never have a body or a banner -
        the split existed and the feature did not.

        `body` is Tiptap JSON, sanitized on write AND on read. Never raw HTML:
        this renders on the UGC domain, where user HTML is the
        highest-consequence vulnerability in the product.
        """
        await self._assert_staff(event_id, roles={EventStaffRole.OWNER, EventStaffRole.MANAGER})

        content = await self._session.get(EventContent, event_id)
        if content is None:
            content = EventContent(event_id=event_id)
            self._session.add(content)

        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(content, key, value)
        await self._session.flush()
        return content

    async def mine(self) -> tuple[list[Event], list[Event]]:
        """Events this user runs, and events they attend.

        Both, because they are different lists to a user and the same person is
        often in both: an organizer attends other people's events too.
        """
        repository = EventRepository(self._session)
        return (
            await repository.staffed_by(self._user_id),
            await repository.attended_by(self._user_id),
        )

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
