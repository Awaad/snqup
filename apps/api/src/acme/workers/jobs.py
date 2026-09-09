"""Job implementations.

Jobs ORCHESTRATE services; they never touch a domain's models or repositories
(enforced by import contract 4). That mirrors the api layer, and it is what
stops the coupling that the domain boundary is supposed to prevent from simply
relocating into the job runner.

Each job takes ids and re-reads from Postgres rather than carrying objects.
That is what makes a job reconstructible after a Valkey loss (ADR-0007), and it
means a job that sat in the queue for an hour acts on current data.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.config import get_settings
from acme.domains.connections.service import ConnectionMaintenance
from acme.domains.events.service import EventDigestService
from acme.domains.identity.service import IdentityService
from acme.domains.notifications.delivery import (
    ROUTING,
    Channel,
    DeviceNotRegisteredError,
    EmailNotifier,
    PushNotifier,
    channels_for,
    within_quiet_hours,
)
from acme.domains.notifications.enums import NotificationKind
from acme.domains.notifications.service import DeviceService, NotificationsService

log = structlog.get_logger()


async def send_due_reminders(session: AsyncSession, now: datetime | None = None) -> int:
    """Follow-up reminders that have come due.

    Writes a `notifications` row, which is the durable record. The push is a
    notification OF that row, and push is best-effort - a missed one must still
    leave the reminder visible in the app.
    """
    at = now or datetime.now(UTC)
    connections = ConnectionMaintenance(session)
    notifications = NotificationsService(session)

    sent = 0
    for view in await connections.due_reminders(at):
        await notifications.create(
            view.user_id, "reminder_due", {"connection_view_id": str(view.id)}
        )
        # Same transaction as the notification: marking first loses the
        # reminder on a crash, marking after sends it twice on a retry.
        await connections.mark_reminder_sent(view, at)
        sent += 1

    await session.flush()
    log.info("reminders.sent", count=sent)
    return sent


async def send_post_event_digests(session: AsyncSession, now: datetime | None = None) -> int:
    """24 hours after an event ends, in the event's LOCAL time.

    The single best retention mechanic in the product: it reactivates the app
    while the connections are still warm and about to go cold. It gets one
    chance per event.
    """
    at = now or datetime.now(UTC)
    events = EventDigestService(session)
    connections = ConnectionMaintenance(session)
    notifications = NotificationsService(session)

    sent = 0
    for event, attendees in await events.due_for_digest(at):
        for user_id in attendees:
            if await notifications.exists(user_id, "post_event_digest", "event_id", str(event.id)):
                continue

            counts = await connections.digest_counts(event.id, user_id)
            if counts["connections"] == 0:
                # "You met 0 people" is a worse message than silence.
                continue

            await notifications.create(
                user_id,
                "post_event_digest",
                {"event_id": str(event.id), "event_name": event.name, **counts},
            )
            sent += 1

    await session.flush()
    log.info("digests.sent", count=sent)
    return sent


async def send_reciprocity_nudges(session: AsyncSession, now: datetime | None = None) -> int:
    """Surface one-sided saves.

    "Sarah saved your card but you haven't saved hers" is a real reason to open
    the app on a Tuesday, which is the whole retention problem for a product
    people otherwise use four times a year.
    """
    at = (now or datetime.now(UTC)) - timedelta(hours=24)
    connections = ConnectionMaintenance(session)
    notifications = NotificationsService(session)

    nudged = 0
    for connection in await connections.pending_older_than(at):
        for user_id in (connection.user_low_id, connection.user_high_id):
            if await notifications.exists(
                user_id, "reciprocity_nudge", "connection_id", str(connection.id)
            ):
                continue
            await notifications.create(
                user_id, "reciprocity_nudge", {"connection_id": str(connection.id)}
            )
            nudged += 1

    await session.flush()
    log.info("nudges.sent", count=nudged)
    return nudged


async def purge_deleted(session: AsyncSession, now: datetime | None = None) -> int:
    """Hard-delete what soft delete only marked.

    THE MOST IMPORTANT JOB HERE, and the easiest to not notice failing. Soft
    delete alone does not satisfy erasure: `deleted_at` without this is a claim
    made to a regulator and not kept (ADR-0020).

    It must ALERT on failure, not merely log - a month of silent failure is
    discovered by an audit rather than by us.

    Erasure deletes the user_profiles row and leaves the `users` anchor, so
    counterparties keep their record of a meeting that happened.
    """
    at = now or datetime.now(UTC)
    identity = IdentityService(session)
    connections = ConnectionMaintenance(session)

    purged = 0
    for user_id in await identity.due_for_purge(at):
        await identity.erase(user_id)
        await connections.purge_for_user(user_id)
        purged += 1

    await session.flush()
    log.info("purge.completed", purged=purged)
    return purged


async def reconcile_billing(session: AsyncSession) -> int:
    """Find entitlements whose subject no longer exists.

    `entitlements.subject_id` is polymorphic with no foreign key, so the
    database cannot catch these and they accumulate silently.

    It ALERTS rather than repairing. Repeated divergence means a webhook
    handler is wrong, and quietly cleaning up would hide the bug that is
    producing the mess (ADR-0009).
    """
    from acme.domains.billing.service import EntitlementsService

    identity = IdentityService(session)
    subjects = await EntitlementsService(session).all_subjects()
    existing = await identity.existing_subject_ids(subjects)

    orphans = subjects - existing
    if orphans:
        log.error(
            "billing.orphaned_entitlements",
            count=len(orphans),
            alert=True,
            sample=[str(o) for o in list(orphans)[:5]],
        )
    return len(orphans)


async def deliver_notifications(session: AsyncSession, now: datetime | None = None) -> int:
    """Send what the other jobs wrote.

    THE MISSING HALF. Every job above created `notifications` rows and nothing
    ever delivered them - the system was write-only, and the only symptom would
    have been users quietly never hearing from us. Nobody reports that.

    Routing is per KIND (notifications/delivery.py): a follow-up reminder is
    push-only because it is worthless an hour late, a post-event digest is
    email because fourteen people is not a push notification.

    Quiet hours DEFER rather than drop: the row stays undelivered and the next
    run picks it up in the morning. Dropping it would lose the reminder
    entirely, which is worse than a late one.
    """
    at = now or datetime.now(UTC)
    notifications = NotificationsService(session)
    identity = IdentityService(session)
    devices = DeviceService(session)

    settings = get_settings()
    senders: dict[Channel, object] = {
        Channel.EMAIL: EmailNotifier(settings.resend_api_key, settings.notification_sender),
        Channel.PUSH: PushNotifier(),
    }

    sent = 0
    for notification in await notifications.undelivered():
        profile = await identity.notification_profile(notification.user_id)
        tokens = await devices.active_tokens(notification.user_id)

        prefs = profile.get("notification_prefs") or {}
        timezone_name = profile.get("timezone")
        channels = channels_for(
            notification.kind,
            prefs=prefs if isinstance(prefs, dict) else {},
            has_device=bool(tokens),
            has_email=bool(profile.get("email")),
            now=at,
            timezone_name=str(timezone_name) if timezone_name else None,
        )

        if not channels:
            # Either genuinely undeliverable, or deferred by quiet hours. The
            # difference matters: deferred must be retried, undeliverable must
            # not.
            if _deferred_by_quiet_hours(notification.kind, profile, at):
                continue
            await notifications.mark_undeliverable(notification, "no eligible channel")
            continue

        # The RECIPIENT's locale, not the server's. Falling back to English is
        # deliberate: a notification in the wrong language still says fourteen
        # people are waiting, while a blank one says nothing.
        subject, body = notifications.render_email(
            notification, str(profile.get("locale") or "") or None
        )

        for channel in channels:
            sender = senders[channel]
            recipients = tokens if channel is Channel.PUSH else [str(profile["email"])]
            for recipient in recipients:
                try:
                    await sender.send(  # type: ignore[attr-defined]
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        # Per (notification, channel, recipient) so a retry
                        # after a timeout does not send a second copy.
                        idempotency_key=(f"{notification.id}:{channel}:{recipient[:24]}"),
                    )
                    await notifications.mark_delivered(notification, channel, at)
                    sent += 1
                except DeviceNotRegisteredError as exc:
                    # Dead token. Revoke it or every future send retries
                    # against a device that no longer exists.
                    await devices.revoke(exc.token)
                    log.info("push.token_revoked", user_id=str(notification.user_id))
                except Exception as exc:
                    await notifications.mark_failed(notification, str(exc))
                    log.warning(
                        "notifications.delivery_failed",
                        notification_id=str(notification.id),
                        channel=str(channel),
                        attempts=notification.delivery_attempts,
                        error=str(exc),
                    )

    await session.flush()
    log.info("notifications.delivered", count=sent)
    return sent


def _deferred_by_quiet_hours(
    kind: NotificationKind, profile: dict[str, object], at: datetime
) -> bool:
    """Whether the only reason nothing sent is that it is the middle of the
    night where this person is."""
    routing = ROUTING.get(kind)
    if routing is None or not routing.respects_quiet_hours:
        return False
    if Channel.PUSH not in routing.channels:
        return False
    timezone_name = profile.get("timezone")
    return within_quiet_hours(at, str(timezone_name) if timezone_name else None)


async def sync_crm_contacts(session: AsyncSession, connection_id: str, view_ids: list[str]) -> int:
    """Push connections to a connected CRM.

    Takes IDS and re-reads, like every job here: reconstructible after a Valkey
    loss, and a job that sat in the queue for an hour acts on current data.

    THE FAILURE THAT MATTERS is a duplicate contact in someone's CRM - the one
    users complain about loudest and cannot easily undo. `crm_synced_contacts`
    stores the provider's id per (connection, view), so a retry UPDATES rather
    than creating.

    An expired grant is not retried. CrmAuthError flags the connection and
    stops; retrying a revoked token forever produces a queue that never drains
    and a user who is never told their sync broke.
    """
    from acme.domains.crm.service import CrmSyncService

    settings = get_settings()
    service = CrmSyncService(session, settings.crm_token_key)
    return await service.push(UUID(connection_id), [UUID(v) for v in view_ids])
