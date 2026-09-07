"""Notifications repository."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, select

from acme.core.repository import TenantScopedRepository
from acme.domains.notifications.enums import NotificationKind
from acme.domains.notifications.models import DeviceToken, Notification


class NotificationRepository(TenantScopedRepository[Notification]):
    """The caller's own inbox. Tenant-scoped, so another user's notifications
    are in rows these queries never select."""

    model = Notification
    user_column = "user_id"
    organization_column = None

    def inbox(self, *, unread_only: bool = False) -> Select[tuple[Notification]]:
        stmt = self.scoped()
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        return stmt.order_by(Notification.created_at.desc())

    async def unread_count(self) -> int:
        stmt = select(func.count()).select_from(
            self.scoped().where(Notification.read_at.is_(None)).subquery()
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def mark_read(self, notification_id: UUID) -> Notification | None:
        notification = await self.get(notification_id)
        if notification is None:
            return None
        notification.read_at = datetime.now(UTC)
        return notification

    async def mark_all_read(self) -> int:
        marked = 0
        now = datetime.now(UTC)
        rows = (await self.session.execute(self.inbox(unread_only=True))).scalars()
        for notification in rows:
            notification.read_at = now
            marked += 1
        return marked

    async def exists(self, user_id: UUID, kind: NotificationKind, key: str, value: str) -> bool:
        """Whether this exact notification was already sent.

        NOT tenant-scoped: the job runner asks on behalf of a user rather than
        as one. The scheduler may run a window twice, and a duplicate digest is
        the kind of thing people unsubscribe over.
        """
        stmt = select(Notification).where(
            Notification.user_id == user_id,
            Notification.kind == kind,
            Notification.payload[key].astext == value,
        )
        return (await self.session.execute(stmt)).first() is not None


class DeviceTokenRepository(TenantScopedRepository[DeviceToken]):
    model = DeviceToken
    user_column = "user_id"
    organization_column = None

    async def active(self) -> list[DeviceToken]:
        stmt = self.scoped().where(DeviceToken.revoked_at.is_(None))
        return list((await self.session.execute(stmt)).scalars())

    async def register(self, expo_token: str, platform: str, locale: str | None) -> DeviceToken:
        """Idempotent on the token.

        The same device re-registers on every launch, and a new row each time
        would fan a single push out to hundreds of stale tokens.
        """
        existing = (
            await self.session.execute(
                select(DeviceToken).where(DeviceToken.expo_token == expo_token)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.user_id = self.tenant.id
            existing.last_seen_at = datetime.now(UTC)
            existing.revoked_at = None
            existing.locale = locale or existing.locale
            return existing

        return self.add(DeviceToken(expo_token=expo_token, platform=platform, locale=locale))
