"""ARQ worker configuration.

Schedules are conservative on purpose. Every job here is idempotent, so running
one twice is harmless; missing a window is not, and a tighter schedule costs
nothing but database reads.
"""

from datetime import datetime
from typing import Any, ClassVar

import structlog
from arq import cron
from arq.connections import RedisSettings

from acme.core.config import get_settings
from acme.core.db import create_engine, create_session_factory
from acme.core.logging import configure_logging
from acme.workers import jobs

log = structlog.get_logger()


async def _run(name: str, ctx: dict[str, Any]) -> int:
    """Run one job in its own transaction.

    Per-job rather than per-run: a failing digest must not roll back reminders
    that already succeeded.
    """
    factory = ctx["session_factory"]
    handler = getattr(jobs, name)
    async with factory() as session:
        try:
            result: int = await handler(session)
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def send_due_reminders(ctx: dict[str, Any]) -> int:
    return await _run("send_due_reminders", ctx)


async def send_post_event_digests(ctx: dict[str, Any]) -> int:
    return await _run("send_post_event_digests", ctx)


async def send_reciprocity_nudges(ctx: dict[str, Any]) -> int:
    return await _run("send_reciprocity_nudges", ctx)


async def deliver_notifications(ctx: dict[str, Any]) -> int:
    """Send what the other jobs wrote.

    Runs OFTEN because a follow-up reminder is worthless an hour late, and
    because quiet hours defer rather than drop - a notification held overnight
    needs a run soon after 08:00 local, and local differs by user.
    """
    return await _run("deliver_notifications", ctx)


async def sync_crm_contacts(ctx: dict[str, Any], connection_id: str, view_ids: list[str]) -> int:
    """Enqueued on demand, not on a schedule.

    A user asking to sync wants it now; a cron would either be too slow to feel
    responsive or run constantly against connections with nothing to push.
    """
    factory = ctx["session_factory"]
    async with factory() as session:
        try:
            result: int = await jobs.sync_crm_contacts(session, connection_id, view_ids)
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def purge_deleted(ctx: dict[str, Any]) -> int:
    """The one that must ALERT on failure, not merely log.

    Soft delete without this is a claim made to a regulator and not kept, and a
    month of silent failure is discovered by an audit (ADR-0020).
    """
    try:
        return await _run("purge_deleted", ctx)
    except Exception:
        log.exception("purge.failed", alert=True)
        raise


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        service="worker",
        env=settings.environment,
        version=settings.version,
    )
    engine = create_engine(settings)
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    """ARQ entry point: `arq acme.workers.settings.WorkerSettings`.

    RETRY AND TIMEOUT are set explicitly. The defaults are wrong for these
    jobs in both directions:

      max_tries    A job that fails because Postgres blipped should retry. One
                   that fails because of a bug should NOT retry forever - it
                   fills the queue and buries the log line that says why.
      job_timeout  A digest run across every event can be slow, but a job that
                   hangs holds a worker slot until restart. A ceiling turns a
                   hang into a visible failure.
      keep_result  Results are kept briefly so a failed run is inspectable in
                   Redis rather than only in logs.

    OVERLAP is prevented by ARQ's cron `job_id` deduplication: a cron job that
    is still running when its next tick arrives is skipped. Without that,
    purge_deleted running long would start a second copy that competes for the
    same rows.
    """

    on_startup = startup
    on_shutdown = shutdown

    #: Three attempts with backoff. Enough for a transient database or network
    #: failure, few enough that a real bug surfaces instead of looping.
    max_tries = 3
    retry_jobs = True

    #: Ten minutes. Every job here is batched, so anything slower is stuck
    #: rather than busy.
    job_timeout = 600

    #: Keep failures visible for an hour without letting results accumulate.
    keep_result = 3600

    #: One worker is enough at this scale, and serial execution means two cron
    #: jobs cannot contend for the same rows.
    max_jobs = 1
    functions: ClassVar = [
        send_due_reminders,
        send_post_event_digests,
        send_reciprocity_nudges,
        deliver_notifications,
        sync_crm_contacts,
        purge_deleted,
    ]
    cron_jobs: ClassVar = [
        # Reminders are time-sensitive to the user: one due at 09:00 that
        # arrives at 10:00 has lost most of its point.
        cron(send_due_reminders, minute=set(range(0, 60, 5))),
        # Digests fire 24h after an event ends in LOCAL time, so this runs
        # hourly to catch every timezone's window.
        cron(send_post_event_digests, minute={7}),
        cron(send_reciprocity_nudges, hour={9}, minute={0}),
        # Every two minutes. Reminders are time-sensitive to the user, and
        # quiet-hours deferrals need a run soon after 08:00 in whatever
        # timezone the recipient is in.
        cron(deliver_notifications, minute=set(range(0, 60, 2))),
        # Daily, off-peak. Batched, so a backlog drains over several runs
        # rather than locking a table for a long delete.
        cron(purge_deleted, hour={3}, minute={0}),
    ]

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(str(get_settings().redis_url))

    @staticmethod
    def now() -> datetime:
        from datetime import UTC

        return datetime.now(UTC)
