"""Events repository."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.events.enums import EventVisibility
from acme.domains.events.models import Event, EventAttendee, EventStaff


class EventRepository:
    """NOT tenant-scoped through the generic base.

    Events have TWO owners - an organization and their own staff - and staff
    membership is the one that decides access (ADR-0018). A day-hire scanner
    has an event role and no organization role at all, so a tenant filter on
    organization_id would lock them out of the one event they were hired for.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_code(self, code: str) -> Event | None:
        stmt = select(Event).where(Event.code == code.strip().upper(), Event.deleted_at.is_(None))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def by_slug(self, slug: str) -> Event | None:
        """Indexable lookup. Only PUBLIC events resolve.

        For the sitemap and anything that decides what search engines see.
        """
        stmt = select(Event).where(
            Event.slug == slug.strip().lower(),
            Event.visibility == EventVisibility.PUBLIC,
            Event.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def by_slug_visible(self, slug: str) -> Event | None:
        """Page lookup. PUBLIC and UNLISTED both resolve.

        The difference from by_slug() is the whole visibility model: an
        unlisted event has a working link and is noindex, a private one does
        not resolve by slug at all (ADR-0008). Two methods rather than a
        boolean argument, because a boolean at a call site is unreadable and
        getting it backwards silently publishes a private event.
        """
        stmt = select(Event).where(
            Event.slug == slug.strip().lower(),
            Event.visibility.in_([EventVisibility.PUBLIC, EventVisibility.UNLISTED]),
            Event.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def staffed_by(self, user_id: UUID) -> list[Event]:
        stmt = (
            select(Event)
            .join(
                EventStaff,
                (EventStaff.event_id == Event.id)
                & (EventStaff.user_id == user_id)
                & (EventStaff.deleted_at.is_(None)),
            )
            .where(Event.deleted_at.is_(None))
            .order_by(Event.starts_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def attended_by(self, user_id: UUID) -> list[Event]:
        stmt = (
            select(Event)
            .join(
                EventAttendee,
                (EventAttendee.event_id == Event.id)
                & (EventAttendee.user_id == user_id)
                & (EventAttendee.deleted_at.is_(None)),
            )
            .where(Event.deleted_at.is_(None))
            .order_by(Event.starts_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def attendee_count(self, event_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(EventAttendee)
            .where(
                EventAttendee.event_id == event_id,
                EventAttendee.deleted_at.is_(None),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())
