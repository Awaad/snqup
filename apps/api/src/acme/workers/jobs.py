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

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.connections.service import ConnectionMaintenance
from acme.domains.events.service import EventDigestService
from acme.domains.identity.service import IdentityService
from acme.domains.notifications.service import NotificationsService

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
