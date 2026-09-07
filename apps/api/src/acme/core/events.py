"""Realtime fan-out over Postgres LISTEN/NOTIFY.

NOT Redis pub/sub (ADR-0006). The organizer dashboard is strictly
one-directional - the server pushes counts and the browser never sends anything
back - so it is SSE over plain HTTP, and Postgres already has the fan-out
primitive. Adding a Redis backplane would mean sticky sessions and a separate
process for a capability the feature does not use.

THE DEPLOYMENT FOOTGUN, and it fails silently: LISTEN cannot run through a
transaction-mode pooler. Supavisor accepts the connection and then never
delivers a notification, which presents as dashboards connecting fine and never
ticking. DATABASE_LISTEN_URL must be a DIRECT connection.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = structlog.get_logger()


#: Postgres channel names are identifiers, so they cannot carry a raw UUID.
#: Hex, no hyphens, prefixed.
def channel_for(event_id: UUID) -> str:
    return f"evt_{event_id.hex}"


async def notify(session: Any, event_id: UUID, payload: dict[str, object]) -> None:
    """Publish inside the caller's transaction.

    Deliberately transactional: NOTIFY is delivered on COMMIT, so a dashboard
    can never learn about an exchange that later rolled back. Getting this
    wrong would show organizers a count that then went down.
    """
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": channel_for(event_id), "payload": json.dumps(payload)},
    )


class EventStream:
    """One listener per worker, fanning out to that worker's SSE clients.

    Each SSE client gets its own queue rather than sharing one, because a slow
    client must not hold up the others - and at a live event on venue wifi,
    slow clients are the normal case.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}
        self._task: asyncio.Task[None] | None = None

    async def subscribe(self, event_id: UUID) -> AsyncGenerator[str]:
        channel = channel_for(event_id)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._subscribers.setdefault(channel, set()).add(queue)

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    # A comment frame, not data. Proxies and load balancers
                    # close idle connections at 30-60s, and an organizer whose
                    # dashboard silently died mid-event has no idea why.
                    yield ": keepalive\n\n"
                    continue
                yield payload
        finally:
            subscribers = self._subscribers.get(channel, set())
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(channel, None)

    def publish(self, channel: str, payload: str) -> None:
        for queue in self._subscribers.get(channel, set()):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop rather than block. The payload is a SNAPSHOT of counts,
                # not a delta, so a client that misses one is corrected by the
                # next - which is why snapshots were chosen over deltas.
                log.warning("sse.queue_full", channel=channel)
