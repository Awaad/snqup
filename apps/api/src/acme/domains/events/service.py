"""Events service."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.events.models import Event, EventAttendee, EventContent


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
