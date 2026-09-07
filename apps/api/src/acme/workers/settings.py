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
    """ARQ entry point: `arq acme.workers.settings.WorkerSettings`."""

    on_startup = startup
    on_shutdown = shutdown
    functions: ClassVar = [
        send_due_reminders,
        send_post_event_digests,
        send_reciprocity_nudges,
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
