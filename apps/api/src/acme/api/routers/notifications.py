"""Notification endpoints.


Every push has an in-app equivalent here on purpose. Push delivery is
best-effort: a missed one, a revoked token or a user who declined the
permission must still leave the reminder visible in the app.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from acme.api.deps import CurrentUserDep, SessionDep
from acme.core.errors import ApiError
from acme.domains.notifications.schemas import (
    DeviceRegistration,
    NotificationListOut,
    NotificationOut,
    NotificationPreferences,
)
from acme.domains.notifications.service import DeviceService, NotificationsService

router = APIRouter(prefix="/v1", tags=["notifications"])


@router.get("/notifications", response_model=NotificationListOut)
async def list_notifications(
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    unread_only: bool = False,
) -> NotificationListOut:
    """The in-app inbox.

    `unread` is returned alongside so a client can render a badge without a
    second request — at an event that round trip happens on every foreground.
    """
    service = NotificationsService(session)
    items = await service.inbox(user.id, limit=limit, unread_only=unread_only)
    return NotificationListOut(
        items=[NotificationOut.model_validate(n) for n in items],
        unread=await service.unread_count(user.id),
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: UUID, session: SessionDep, user: CurrentUserDep) -> None:
    marked = await NotificationsService(session).mark_read(user.id, notification_id)
    if not marked:
        # Same code whether it does not exist or belongs to someone else.
        raise ApiError("NOTIFICATION_NOT_FOUND", status_code=404)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(session: SessionDep, user: CurrentUserDep) -> None:
    await NotificationsService(session).mark_all_read(user.id)


@router.get("/notifications/preferences", response_model=NotificationPreferences)
async def get_preferences(session: SessionDep, user: CurrentUserDep) -> NotificationPreferences:
    return NotificationPreferences(
        preferences=await NotificationsService(session).preferences(user.id)
    )


@router.put("/notifications/preferences", response_model=NotificationPreferences)
async def set_preferences(
    payload: NotificationPreferences, session: SessionDep, user: CurrentUserDep
) -> NotificationPreferences:
    """Per-kind channel opt-outs.

    Transactional notifications are NOT listed here and cannot be disabled — a
    CRM that stopped syncing means the user believes their contacts are safe
    when they are not, and suppressing that would be a disservice dressed up as
    a preference.
    """
    saved = await NotificationsService(session).set_preferences(user.id, payload.preferences)
    return NotificationPreferences(preferences=saved)


@router.post("/devices", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
async def register_device(
    payload: DeviceRegistration, session: SessionDep, user: CurrentUserDep
) -> None:
    """Register for push.

    Do NOT call this on first launch. Ask after the first successful exchange,
    when the value is obvious — iOS permits the prompt once, and a cold ask
    gets roughly 40% with no second chance.

    Idempotent on the token: the same device re-registers on every launch, and
    a row per launch would fan one push out to hundreds of stale tokens.
    """
    await DeviceService(session).register(
        user.id,
        expo_token=payload.expo_token,
        platform=payload.platform,
        locale=payload.locale,
    )


@router.delete("/devices/{expo_token}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(expo_token: str, session: SessionDep, user: CurrentUserDep) -> None:
    """Sign-out, or a user turning push off.

    Revoked rather than deleted so the same device re-registering is an update
    rather than a new row.
    """
    await DeviceService(session).revoke_for_user(user.id, expo_token)
