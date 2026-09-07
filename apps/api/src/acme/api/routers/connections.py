"""Connection list endpoints.

The retention surface. Everything here operates on the caller's OWN views, so
another participant's notes are not merely filtered out - they live in rows
these queries never select (ADR-0003).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.connections.schemas import (
    ConnectionListOut,
    ConnectionOut,
    ConnectionUpdate,
    MergeRequest,
)
from acme.domains.connections.service import ConnectionListService

router = APIRouter(prefix="/v1", tags=["connections"])


@router.get("/connections", response_model=ConnectionListOut)
async def list_connections(
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
    event_id: UUID | None = None,
    tag: str | None = None,
    include_archived: bool = False,
) -> ConnectionListOut:
    """The contact list. NEVER gated.

    Connection history is free forever (00-context/pricing.md): hiding contacts
    someone already made reads as theft. Export, analytics and the reminder cap
    are the paid surfaces.

    Cursor-paginated, never offset - offset skips and duplicates rows as new
    connections are inserted, and this table only grows.
    """
    return await ConnectionListService(session, user.id).page(
        limit=limit,
        cursor=cursor,
        event_id=event_id,
        tag=tag,
        include_archived=include_archived,
    )


@router.get("/connections/duplicates", response_model=dict[UUID, list[UUID]])
async def find_duplicates(session: SessionDep, user: CurrentUserDep) -> dict[UUID, list[UUID]]:
    """Groups of views with the same counterpart.

    Surfaced rather than merged automatically: meeting someone at two events is
    legitimately two records until the user says otherwise.
    """
    return await ConnectionListService(session, user.id).find_duplicates()


@router.get("/connections/{view_id}", response_model=ConnectionOut)
async def get_connection(view_id: UUID, session: SessionDep, user: CurrentUserDep) -> ConnectionOut:
    return await ConnectionListService(session, user.id).get(view_id)


@router.patch("/connections/{view_id}", response_model=ConnectionOut)
async def update_connection(
    view_id: UUID,
    payload: ConnectionUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> ConnectionOut:
    """Notes, tags, reminders and archiving.

    All of it lands on the caller's own view. The counterpart never sees any of
    it, which is the structural guarantee rather than a filtering decision.
    """
    return await ConnectionListService(session, user.id).update(view_id, payload)


@router.post("/connections/{view_id}/merge", response_model=ConnectionOut)
async def merge_connections(
    view_id: UUID, payload: MergeRequest, session: SessionDep, user: CurrentUserDep
) -> ConnectionOut:
    """Fold duplicates into one contact.

    Only the caller's VIEWS merge. The edges stay, because each records a real
    meeting that happened, and notes are concatenated rather than discarded.
    """
    return await ConnectionListService(session, user.id).merge(view_id, payload.source_ids)


@router.delete("/connections/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(view_id: UUID, session: SessionDep, user: CurrentUserDep) -> None:
    """Remove the caller's copy.

    Asymmetric by design: the edge survives until both participants delete
    their view. My removing a contact must not erase your record of the same
    meeting.
    """
    await ConnectionListService(session, user.id).delete(view_id)
