"""Events repository."""

from datetime import datetime
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
        """Public lookup. Only PUBLIC events resolve.

        Unlisted events have a working link but must not be discoverable, and
        private ones must not resolve by slug at all (ADR-0008).
        """
        stmt = select(Event).where(
            Event.slug == slug.strip().lower(),
            Event.visibility == EventVisibility.PUBLIC,
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

    async def upcoming_public(self, after: datetime, limit: int = 50) -> list[Event]:
        stmt = (
            select(Event)
            .where(
                Event.visibility == EventVisibility.PUBLIC,
                Event.starts_at >= after,
                Event.deleted_at.is_(None),
            )
            .order_by(Event.starts_at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())
