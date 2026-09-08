"""Enqueueing work from the API.

Two things genuinely need it.

  GDPR export   assembling every card, connection and note for an account is
                slow and unbounded. Doing it inline holds a request open for
                as long as the account is large, and times out for exactly the
                users who most need it to work.
  CRM sync      pushing contacts to a third party puts THEIR latency and
                THEIR outage inside our request. A sync triggered from the app
                must return immediately and report progress separately.

THE RULE FROM ADR-0007: every job must be reconstructible from Postgres.
Enqueue IDs, never objects. A total Valkey loss then means jobs are re-enqueued
from their database source rather than lost - which is why `data_export_requests`
is a table and not just a queue entry.

Enqueue FAILURES ARE NOT SILENT. If Valkey is down, the caller is told rather
than being handed a request id that will never complete. That is the opposite
of the fail-open rule for rate limiting, and deliberately so: a dropped rate
limit degrades a defence, a dropped job loses work the user asked for and
believes is happening.
"""

from typing import Any
from uuid import UUID

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from acme.core.errors import ApiError

log = structlog.get_logger()


async def create_job_pool(redis_url: str) -> ArqRedis:
    """Connection pool for enqueueing. Held on app.state for the process life."""
    return await create_pool(RedisSettings.from_dsn(redis_url))


class JobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, function: str, *args: Any, job_id: str | None = None) -> str:
        """Queue one job.

        `job_id` deduplicates: ARQ refuses a job whose id is already queued or
        running. Passing a deterministic id is what stops a user who taps
        "export my data" three times from queueing three exports.
        """
        try:
            job = await self._pool.enqueue_job(function, *args, _job_id=job_id)
        except Exception as exc:
            # Loud, not silent. Returning a request id for work that will never
            # run is worse than an error, because the user waits for something
            # that is not coming.
            log.error("jobs.enqueue_failed", function=function, error=str(exc))
            raise ApiError(
                "SERVICE_UNAVAILABLE",
                status_code=503,
                message="could not queue background work; retry shortly",
            ) from exc

        if job is None:
            # Already queued under this id. Not an error - it is the
            # deduplication working, and the caller wanted the work done once.
            log.info("jobs.already_queued", function=function, job_id=job_id)
            return job_id or ""
        return job.job_id

    async def request_data_export(self, request_id: UUID, user_id: UUID) -> str:
        """GDPR Article 20. Deduplicated per REQUEST, not per user.

        Per request rather than per user because a second export asked for a
        week later is legitimate; a double-tap on the same one is not.
        """
        return await self.enqueue(
            "build_data_export",
            str(request_id),
            str(user_id),
            job_id=f"export:{request_id}",
        )

    async def sync_crm_contacts(self, connection_id: UUID, view_ids: list[UUID]) -> str:
        """Push contacts to a connected CRM.

        Deduplicated per CRM connection, so a user hammering "sync now" gets
        one run rather than a queue of overlapping pushes competing to write
        the same contacts.
        """
        return await self.enqueue(
            "sync_crm_contacts",
            str(connection_id),
            [str(v) for v in view_ids],
            job_id=f"crmsync:{connection_id}",
        )
