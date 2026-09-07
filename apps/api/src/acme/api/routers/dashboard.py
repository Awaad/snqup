"""The live organizer dashboard.

SSE, not WebSockets (ADR-0006). The stream is one-directional, so a plain HTTP
GET that stays open does the job: it reconnects automatically via EventSource in
every browser, passes through every proxy that handles HTTP, and needs no
backplane.

This is the feature organizers demo on a projector at the venue, so it has to
survive bad wifi.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.events.service import EventAdminService

router = APIRouter(prefix="/v1", tags=["events"])

# How often the stream re-reads and pushes a snapshot even with no activity.
# The dashboard must not look frozen during a quiet stretch, and an organizer
# cannot tell "nothing happening" from "it broke".
SNAPSHOT_INTERVAL_SECONDS = 15


@router.get("/events/{event_id}/stream")
async def event_stream(
    event_id: UUID, request: Request, session: SessionDep, user: CurrentUserDep
) -> StreamingResponse:
    """Live aggregate counts.

    Authorization happens ONCE, before the stream opens - an open connection
    cannot be re-checked, so a revoked staff member keeps the stream until they
    disconnect. Acceptable because the payload is aggregates only, and it is
    documented rather than assumed.

    Payload is a full SNAPSHOT, never a delta. That makes a dropped message
    self-correcting: reconnect and you have the truth, with no replay log to
    maintain.

    AGGREGATES ONLY (ADR-0012) - nothing here reveals which attendee connected
    with which, and everything is suppressed below a cohort of 10.
    """
    service = EventAdminService(session, user.id)
    # Raises EVENT_NOT_FOUND before any streaming response is started, so an
    # unauthorised caller gets a normal error rather than an empty stream.
    await service.stats(event_id)

    async def generate() -> AsyncGenerator[str]:
        while True:
            if await request.is_disconnected():
                break
            stats = await service.stats(event_id)
            yield f"event: stats\ndata: {json.dumps(stats.model_dump())}\n\n"
            await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # nginx buffers streaming responses by default, which turns a live
            # dashboard into one that updates in bursts minutes apart.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
