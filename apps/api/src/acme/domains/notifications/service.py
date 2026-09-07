"""Notifications service.

The in-app record is the durable one; a push is a notification OF a row here.
Push delivery is best-effort, so a missed push must still leave the thing
visible in the app.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.notifications.models import Notification


class NotificationsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: UUID, kind: str, payload: dict[str, object]) -> Notification:
        notification = Notification(user_id=user_id, kind=kind, payload=payload)
        self._session.add(notification)
        await self._session.flush()
        return notification

    async def exists(self, user_id: UUID, kind: str, key: str, value: str) -> bool:
        """Whether this exact notification was already sent.

        The scheduler may run a window more than once, and a duplicate digest
        is the kind of thing people unsubscribe over.
        """
        stmt = select(Notification).where(
            Notification.user_id == user_id,
            Notification.kind == kind,
            Notification.payload[key].astext == value,
        )
        return (await self._session.execute(stmt)).first() is not None
