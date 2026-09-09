"""Notifications service.

The in-app record is the durable one; a push is a notification OF a row here.
Push delivery is best-effort, so a missed push must still leave the thing
visible in the app.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.notifications.delivery import MAX_DELIVERY_ATTEMPTS, Channel
from acme.domains.notifications.enums import NotificationKind
from acme.domains.notifications.models import DeviceToken, Notification


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

    async def undelivered(self, limit: int = 200) -> list[Notification]:
        """The delivery worker's queue.

        Excludes anything past the attempt ceiling: a notification that cannot
        succeed must stop being retried, or it buries the ones that can.
        """
        stmt = (
            select(Notification)
            .where(Notification.pushed_at.is_(None))
            .where(Notification.emailed_at.is_(None))
            .where(Notification.delivery_attempts < MAX_DELIVERY_ATTEMPTS)
            .order_by(Notification.created_at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def mark_delivered(
        self, notification: Notification, channel: Channel, at: datetime
    ) -> None:
        if channel is Channel.PUSH:
            notification.pushed_at = at
        else:
            notification.emailed_at = at
        notification.delivery_error = None

    async def mark_failed(self, notification: Notification, error: str) -> None:
        """Count the attempt and keep the reason.

        The counter is what stops an unroutable notification retrying forever;
        the reason is what tells support why a user never heard from us.
        """
        notification.delivery_attempts += 1
        notification.delivery_error = error[:500]

    async def mark_undeliverable(self, notification: Notification, reason: str) -> None:
        """Nothing about this will change, so stop.

        A user with no device token and no email, or a kind they have turned
        off entirely. Treated as done rather than retried - the alternative is
        a queue that never drains for a reason no one can act on.
        """
        now = datetime.now(UTC)
        notification.pushed_at = now
        notification.emailed_at = now
        notification.delivery_error = reason

    async def inbox(
        self, user_id: UUID, *, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars())

    async def unread_count(self, user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def mark_read(self, user_id: UUID, notification_id: UUID) -> bool:
        """Scoped by user, not just by id.

        Without the user filter, anyone holding an id could mark someone else's
        notification read - which is minor on its own and confirms the id
        exists, which is not.
        """
        stmt = select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
        notification = (await self._session.execute(stmt)).scalar_one_or_none()
        if notification is None:
            return False
        notification.read_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def mark_all_read(self, user_id: UUID) -> int:
        now = datetime.now(UTC)
        stmt = select(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
        marked = 0
        for notification in (await self._session.execute(stmt)).scalars():
            notification.read_at = now
            marked += 1
        await self._session.flush()
        return marked

    async def preferences(self, user_id: UUID) -> dict[str, dict[str, bool]]:
        from acme.domains.identity.service import IdentityService

        profile = await IdentityService(self._session).notification_profile(user_id)
        prefs = profile.get("notification_prefs") or {}
        return prefs if isinstance(prefs, dict) else {}

    async def set_preferences(
        self, user_id: UUID, preferences: dict[str, dict[str, bool]]
    ) -> dict[str, dict[str, bool]]:
        """Reject unknown kinds rather than storing them.

        A typo would otherwise be silently persisted and appear to work, while
        the notification it was meant to disable keeps arriving.
        """
        from acme.core.errors import ApiError

        unknown = set(preferences) - {str(k) for k in NotificationKind}
        if unknown:
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message=f"unknown notification kinds: {sorted(unknown)}",
            )

        from acme.domains.identity.service import IdentityService

        return await IdentityService(self._session).set_notification_prefs(user_id, preferences)

    @staticmethod
    def render_email(notification: Notification, locale: str | None = None) -> tuple[str, str]:
        """Subject and body, IN THE RECIPIENT'S LOCALE.

        The locale argument is the fix for a real bug: this used to render a
        single English dictionary while its docstring claimed to use the
        profile locale. A German user received English email.

        Exposed on the SERVICE because the delivery worker is an entry point
        and may not import a domain's models (import contract 4). The worker
        asks for rendered text rather than being handed a row.

        For `ar` the body must be wrapped in `dir="rtl"` - see
        templates.is_rtl().
        """
        from acme.domains.notifications.templates import body_for, subject_for

        return subject_for(notification, locale), body_for(notification, locale)


class DeviceService:
    """Push tokens.

    Not tenant-scoped: the delivery worker acts on behalf of a user rather than
    as one, and a token has to be revocable by its VALUE when a provider says
    the device is gone.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_tokens(self, user_id: UUID) -> list[str]:
        stmt = select(DeviceToken.expo_token).where(
            DeviceToken.user_id == user_id,
            DeviceToken.revoked_at.is_(None),
        )
        return list((await self._session.execute(stmt)).scalars())

    async def register(
        self, user_id: UUID, *, expo_token: str, platform: str, locale: str | None
    ) -> DeviceToken:
        """Idempotent on the token.

        The same device re-registers on every launch, and a new row each time
        would fan one push out to hundreds of stale tokens.
        """
        existing = (
            await self._session.execute(
                select(DeviceToken).where(DeviceToken.expo_token == expo_token)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.user_id = user_id
            existing.last_seen_at = datetime.now(UTC)
            existing.revoked_at = None
            existing.locale = locale or existing.locale
            await self._session.flush()
            return existing

        token = DeviceToken(
            user_id=user_id,
            expo_token=expo_token,
            platform=platform,
            locale=locale,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def revoke_for_user(self, user_id: UUID, expo_token: str) -> None:
        """Sign-out. Scoped by user so one account cannot revoke another's
        device."""
        existing = (
            await self._session.execute(
                select(DeviceToken).where(
                    DeviceToken.expo_token == expo_token,
                    DeviceToken.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.revoked_at = datetime.now(UTC)
            await self._session.flush()

    async def revoke(self, expo_token: str) -> None:
        """Called when the provider says the device is gone.

        Retrying a dead token forever is how a push queue stops draining.
        """
        existing = (
            await self._session.execute(
                select(DeviceToken).where(DeviceToken.expo_token == expo_token)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.revoked_at = datetime.now(UTC)
            await self._session.flush()
