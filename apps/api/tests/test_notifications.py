"""Notification delivery.

The routing tests matter most. "Send everything everywhere" is how an app gets
muted, and a muted app loses the reminder feature — which is the main reason
this product gets opened between events.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.identity.models import User, UserProfile
from acme.domains.notifications.delivery import (
    Channel,
    EmailNotifier,
    channels_for,
    within_quiet_hours,
)
from acme.domains.notifications.enums import NotificationKind
from acme.domains.notifications.service import DeviceService, NotificationsService

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession, name: str, **profile: object) -> User:
    user = User()
    session.add(user)
    await session.flush()
    session.add(
        UserProfile(
            user_id=user.id,
            auth_subject=f"n-{name}",
            email=f"{name}@example.com",
            **profile,  # type: ignore[arg-type]
        )
    )
    await session.flush()
    return user


class TestQuietHours:
    @pytest.mark.parametrize(
        ("hour", "expected"),
        [(23, True), (3, True), (7, True), (8, False), (14, False), (21, False)],
    )
    def test_the_window_wraps_midnight(self, hour: int, expected: bool) -> None:
        at = datetime(2026, 6, 1, hour, 0, tzinfo=UTC)
        assert within_quiet_hours(at, "UTC") is expected

    def test_it_uses_local_time_not_server_time(self) -> None:
        """A user in Auckland and one in Nicosia do not share a night.

        Falling back to UTC is wrong for most of the world, which is why the
        profile carries a timezone.
        """
        at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert within_quiet_hours(at, "UTC") is False
        # Midnight in Auckland.
        assert within_quiet_hours(at, "Pacific/Auckland") is True


class TestRouting:
    def _channels(self, kind: NotificationKind, **kwargs: object) -> frozenset[Channel]:
        defaults: dict[str, object] = {
            "prefs": {},
            "has_device": True,
            "has_email": True,
            "now": datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
            "timezone_name": "UTC",
        }
        defaults.update(kwargs)
        return channels_for(kind, **defaults)  # type: ignore[arg-type]

    def test_a_reminder_is_push_only(self) -> None:
        """An email reminder to follow up with one person is inbox noise."""
        assert self._channels(NotificationKind.REMINDER_DUE) == frozenset({Channel.PUSH})

    def test_a_digest_uses_both(self) -> None:
        """Fourteen people is not a push notification; it is something you open
        on a laptop on Monday."""
        assert self._channels(NotificationKind.POST_EVENT_DIGEST) == frozenset(
            {Channel.PUSH, Channel.EMAIL}
        )

    def test_preferences_suppress_a_channel(self) -> None:
        assert (
            self._channels(
                NotificationKind.RECIPROCITY_NUDGE,
                prefs={"reciprocity_nudge": {"push": False}},
            )
            == frozenset()
        )

    def test_a_transactional_notification_ignores_preferences(self) -> None:
        """A CRM that stopped syncing means the user believes their contacts
        are safe when they are not. Suppressing that would be a disservice
        dressed up as a preference.
        """
        assert self._channels(
            NotificationKind.CRM_SYNC_FAILED,
            prefs={"crm_sync_failed": {"email": False}},
        ) == frozenset({Channel.EMAIL})

    def test_quiet_hours_suppress_push_but_not_email(self) -> None:
        """Deferred, not dropped: the row stays undelivered and the next run
        picks it up in the morning."""
        night = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
        assert self._channels(NotificationKind.POST_EVENT_DIGEST, now=night) == frozenset(
            {Channel.EMAIL}
        )

    def test_an_urgent_announcement_ignores_quiet_hours(self) -> None:
        """An event may legitimately run past 22:00, and a room change at
        22:30 is exactly when it matters."""
        night = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
        assert Channel.PUSH in self._channels(NotificationKind.EVENT_ANNOUNCEMENT, now=night)

    def test_no_device_means_no_push(self) -> None:
        assert self._channels(NotificationKind.REMINDER_DUE, has_device=False) == frozenset()

    def test_no_email_means_no_email_channel(self) -> None:
        """An erased user's profile is gone but their notification rows may
        remain. Attempting delivery would be useless and a disclosure."""
        assert self._channels(NotificationKind.CRM_SYNC_FAILED, has_email=False) == frozenset()

    def test_quiet_hours_never_suppress_email(self) -> None:
        """Email has no quiet hours - an inbox is read when the person chooses.

        Suppressing it would DELAY the digest for no benefit, and the digest is
        the single best retention mechanic in the product.
        """
        night = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
        assert self._channels(
            NotificationKind.POST_EVENT_DIGEST, now=night, has_device=False
        ) == frozenset({Channel.EMAIL})

    def test_an_unrouted_kind_sends_nothing(self) -> None:
        """A bug, not a reason to guess. Sending it everywhere would be the
        wrong default."""

        class Fake:
            def __str__(self) -> str:
                return "not_a_real_kind"

        assert (
            channels_for(
                Fake(),  # type: ignore[arg-type]
                prefs={},
                has_device=True,
                has_email=True,
                now=datetime(2026, 6, 1, 14, 0, tzinfo=UTC),
                timezone_name="UTC",
            )
            == frozenset()
        )


class TestEmailNotifier:
    async def test_a_missing_key_raises_rather_than_dropping(self) -> None:
        """A notification nobody receives is worse than a loud failure -
        nobody reports the first one."""
        with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
            await EmailNotifier("", "from@example.com").send(
                recipient="to@example.com",
                subject="s",
                body="b",
                idempotency_key="k",
            )


class TestInbox:
    async def test_notifications_are_readable(self, session: AsyncSession) -> None:
        """They were not. Jobs wrote rows and no endpoint could fetch them."""
        user = await _user(session, "inbox")
        service = NotificationsService(session)
        await service.create(user.id, NotificationKind.REMINDER_DUE, {"x": "1"})

        items = await service.inbox(user.id)
        assert len(items) == 1
        assert await service.unread_count(user.id) == 1

    async def test_marking_read_is_scoped_to_the_owner(self, session: AsyncSession) -> None:
        """Without the user filter, anyone holding an id could mark someone
        else's notification read - minor on its own, and it confirms the id
        exists, which is not."""
        owner = await _user(session, "own")
        other = await _user(session, "other")
        service = NotificationsService(session)
        notification = await service.create(owner.id, NotificationKind.REMINDER_DUE, {})

        assert await service.mark_read(other.id, notification.id) is False
        assert await service.mark_read(owner.id, notification.id) is True

    async def test_preferences_reject_an_unknown_kind(self, session: AsyncSession) -> None:
        """A typo would otherwise be stored silently and appear to work, while
        the notification it was meant to disable keeps arriving."""
        user = await _user(session, "prefs")
        with pytest.raises(ApiError):
            await NotificationsService(session).set_preferences(
                user.id, {"reminder_dew": {"push": False}}
            )

    async def test_preferences_round_trip(self, session: AsyncSession) -> None:
        user = await _user(session, "prefs2")
        service = NotificationsService(session)
        await service.set_preferences(user.id, {"reminder_due": {"push": False}})
        assert await service.preferences(user.id) == {"reminder_due": {"push": False}}


class TestDevices:
    async def test_registration_is_idempotent(self, session: AsyncSession) -> None:
        """The same device re-registers on every launch, and a row per launch
        would fan one push out to hundreds of stale tokens."""
        user = await _user(session, "dev")
        service = DeviceService(session)

        await service.register(
            user.id, expo_token="ExponentPushToken[abc]", platform="ios", locale="en"
        )
        await service.register(
            user.id, expo_token="ExponentPushToken[abc]", platform="ios", locale="de"
        )

        assert await service.active_tokens(user.id) == ["ExponentPushToken[abc]"]

    async def test_a_revoked_token_stops_receiving(self, session: AsyncSession) -> None:
        """Retrying a dead token forever is how a push queue stops draining."""
        user = await _user(session, "dev2")
        service = DeviceService(session)
        await service.register(
            user.id, expo_token="ExponentPushToken[xyz]", platform="android", locale=None
        )

        await service.revoke("ExponentPushToken[xyz]")
        assert await service.active_tokens(user.id) == []

    async def test_one_user_cannot_revoke_anothers_device(self, session: AsyncSession) -> None:
        owner = await _user(session, "dev3")
        other = await _user(session, "dev4")
        service = DeviceService(session)
        await service.register(
            owner.id, expo_token="ExponentPushToken[own]", platform="ios", locale=None
        )

        await service.revoke_for_user(other.id, "ExponentPushToken[own]")
        assert await service.active_tokens(owner.id) == ["ExponentPushToken[own]"]


class TestDeliveryQueue:
    async def test_undelivered_excludes_exhausted_attempts(self, session: AsyncSession) -> None:
        """A notification that cannot succeed must stop being retried, or it
        buries the ones that can."""
        user = await _user(session, "queue")
        service = NotificationsService(session)
        stuck = await service.create(user.id, NotificationKind.REMINDER_DUE, {})
        stuck.delivery_attempts = 5
        await session.flush()

        assert stuck.id not in {n.id for n in await service.undelivered()}

    async def test_marking_one_channel_leaves_the_other_pending(
        self, session: AsyncSession
    ) -> None:
        """Push and email fail independently and for different reasons.
        Retrying both because one failed would double-send."""
        user = await _user(session, "queue2")
        service = NotificationsService(session)
        notification = await service.create(user.id, NotificationKind.POST_EVENT_DIGEST, {})

        await service.mark_delivered(notification, Channel.PUSH, datetime.now(UTC))
        assert notification.pushed_at is not None
        assert notification.emailed_at is None

    async def test_an_undeliverable_notification_is_not_retried(
        self, session: AsyncSession
    ) -> None:
        """No device and no email. Nothing about it will change, so retrying
        forever is a queue that never drains for a reason nobody can act on."""
        user = await _user(session, "queue3")
        service = NotificationsService(session)
        notification = await service.create(user.id, NotificationKind.REMINDER_DUE, {})

        await service.mark_undeliverable(notification, "no eligible channel")
        await session.flush()

        assert notification.id not in {n.id for n in await service.undelivered()}
