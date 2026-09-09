"""Notification delivery: email and push.

TWO CHANNELS, ROUTED BY KIND, not by preference alone:

  push   immediate and time-sensitive. A follow-up reminder is worthless an
         hour late, and push is the only channel that arrives in seconds.
  email  substantial and re-readable. A post-event digest listing fourteen
         people is not a push notification; it is something you open on a
         laptop on Monday.

Some notifications use both, some neither. The routing table is explicit
because "send everything everywhere" is how an app gets muted.

CONSENT. Transactional notifications (a billing failure, a security event) send
regardless of marketing consent — they are not marketing and suppressing them
would break the account. Everything else respects per-kind preferences, and
every non-transactional email carries an unsubscribe link. That is a legal
requirement, not a courtesy.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx
import structlog

from acme.domains.notifications.enums import NotificationKind

log = structlog.get_logger()

RESEND_API = "https://api.resend.com/emails"
EXPO_PUSH_API = "https://exp.host/--/api/v2/push/send"

#: Stop after this many failures. Without a ceiling, a permanently failing
#: notification retries forever, fills the queue, and buries the ones that
#: could still succeed.
MAX_DELIVERY_ATTEMPTS = 5

#: No push between these hours, in the RECIPIENT's local time. A follow-up
#: reminder at 3am is worse than no reminder: it wakes someone and teaches them
#: to disable notifications, which costs every future one.
QUIET_START, QUIET_END = time(22, 0), time(8, 0)


class Channel(StrEnum):
    PUSH = "push"
    EMAIL = "email"


@dataclass(frozen=True, slots=True)
class Routing:
    channels: frozenset[Channel]
    #: Transactional notifications ignore preferences and marketing consent.
    #: They are not marketing, and suppressing them breaks the account.
    transactional: bool = False
    #: Whether a late delivery is worse than none. Quiet hours DEFER a
    #: reminder to morning; they DROP nothing.
    respects_quiet_hours: bool = True


ROUTING: dict[NotificationKind, Routing] = {
    # Time-sensitive and personal. Push only - an email reminder to follow up
    # with one person is noise in an inbox.
    NotificationKind.REMINDER_DUE: Routing(frozenset({Channel.PUSH})),
    # Substantial and re-readable. Email, plus a push to say it has arrived.
    NotificationKind.POST_EVENT_DIGEST: Routing(frozenset({Channel.EMAIL, Channel.PUSH})),
    # The softest nudge in the product. Push only, and easy to turn off.
    NotificationKind.RECIPROCITY_NUDGE: Routing(frozenset({Channel.PUSH})),
    # Someone scanned your badge and is waiting. Push, because acting on it
    # while you are still at the event is the entire point.
    NotificationKind.PENDING_REQUEST: Routing(frozenset({Channel.PUSH})),
    # Organizer announcements can be urgent ("room change"), so they ignore
    # quiet hours - an event may legitimately run past 22:00.
    NotificationKind.EVENT_ANNOUNCEMENT: Routing(
        frozenset({Channel.PUSH, Channel.EMAIL}), respects_quiet_hours=False
    ),
    # TRANSACTIONAL. A CRM that stopped syncing means the user believes their
    # contacts are safe when they are not, so this sends regardless of
    # preferences and outside quiet hours.
    NotificationKind.CRM_SYNC_FAILED: Routing(
        frozenset({Channel.EMAIL}), transactional=True, respects_quiet_hours=False
    ),
}


def within_quiet_hours(at: datetime, timezone_name: str | None) -> bool:
    """Local time, not server time.

    A user in Auckland and one in Nicosia do not share a night. Falling back to
    UTC is wrong for most of the world, which is why user_profiles carries a
    timezone.
    """
    zone = ZoneInfo(timezone_name) if timezone_name else UTC
    local = at.astimezone(zone).time()
    if QUIET_START <= QUIET_END:
        return QUIET_START <= local < QUIET_END
    # The window wraps midnight, which is the normal case.
    return local >= QUIET_START or local < QUIET_END


def channels_for(
    kind: NotificationKind,
    *,
    prefs: dict[str, dict[str, bool]],
    has_device: bool,
    has_email: bool,
    now: datetime,
    timezone_name: str | None,
) -> frozenset[Channel]:
    """Which channels this notification should actually use.

    Returns empty when it should not be sent at all — the caller treats that as
    delivered rather than retrying, because nothing about it will change.
    """
    routing = ROUTING.get(kind)
    if routing is None:
        # An unrouted kind is a bug, not a reason to guess. Sending it
        # everywhere would be the wrong default.
        log.error("notifications.unrouted_kind", kind=str(kind))
        return frozenset()

    channels = set(routing.channels)

    if not routing.transactional:
        kind_prefs = prefs.get(str(kind), {})
        channels = {c for c in channels if kind_prefs.get(str(c), True)}

    if Channel.PUSH in channels and not has_device:
        channels.discard(Channel.PUSH)
    if Channel.EMAIL in channels and not has_email:
        channels.discard(Channel.EMAIL)

    if (
        Channel.PUSH in channels
        and routing.respects_quiet_hours
        and within_quiet_hours(now, timezone_name)
    ):
        # DEFERRED, not dropped. The caller leaves the row undelivered and the
        # next run picks it up in the morning.
        channels.discard(Channel.PUSH)

    return frozenset(channels)


class Notifier(Protocol):
    """One channel. Kept narrow so a provider swap is one file."""

    channel: Channel

    async def send(self, *, recipient: str, subject: str, body: str, idempotency_key: str) -> None:
        """Deliver, or raise.

        Raising is what makes the attempt counter meaningful: a silent failure
        would mark the notification delivered and the user would never hear
        from us.
        """
        ...


class EmailNotifier:
    """Resend.

    The API key comes from configuration and the sending domain must be
    verified — which is blocked on the product name, like the OAuth apps. The
    CODE is complete; only the credential is missing.
    """

    channel = Channel.EMAIL

    def __init__(self, api_key: str, sender: str) -> None:
        self._api_key = api_key
        self._sender = sender

    async def send(self, *, recipient: str, subject: str, body: str, idempotency_key: str) -> None:
        if not self._api_key:
            raise RuntimeError(
                "RESEND_API_KEY is not set. Refusing to silently drop email - "
                "a notification nobody receives is worse than a loud failure."
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                RESEND_API,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    # Resend honours this, so a retry after a timeout does not
                    # send a second copy of the same digest.
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "from": self._sender,
                    "to": [recipient],
                    "subject": subject,
                    "html": body,
                },
            )
        if response.status_code >= 400:
            raise RuntimeError(f"resend rejected the message: {response.status_code}")


class PushNotifier:
    """Expo push.

    No credentials needed, which is why this half can be exercised today.

    Expo returns per-message tickets rather than failing the request, so a 200
    does NOT mean delivered. A DeviceNotRegisteredError ticket means the token is
    dead and must be revoked, or every future send retries against a device
    that no longer exists.
    """

    channel = Channel.PUSH

    async def send(self, *, recipient: str, subject: str, body: str, idempotency_key: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                EXPO_PUSH_API,
                json=[{"to": recipient, "title": subject, "body": body}],
            )
        if response.status_code >= 400:
            raise RuntimeError(f"expo rejected the push: {response.status_code}")

        for ticket in response.json().get("data", []):
            if ticket.get("status") != "error":
                continue
            detail = ticket.get("details", {}).get("error")
            if detail == "DeviceNotRegisteredError":
                raise DeviceNotRegisteredError(recipient)
            raise RuntimeError(f"expo push failed: {detail}")


class DeviceNotRegisteredError(Exception):
    """The token is dead. Revoke it rather than retrying.

    Distinct from a transient failure on purpose: retrying a dead token forever
    is how a push queue stops draining.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        super().__init__(f"device token no longer registered: {token[:12]}...")
