"""Notifications request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from acme.domains.notifications.enums import NotificationKind


class NotificationOut(BaseModel):
    """The in-app record.

    `kind` is what the client switches on; the wording lives in its locale
    files, never here (ADR-0011). `payload` carries the ids needed to deep-link
    to whatever the notification is about.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: NotificationKind
    payload: dict[str, object]
    read_at: datetime | None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread: int


class DeviceRegistration(BaseModel):
    """Register for push.

    Do NOT call this on first launch. Ask after the first successful exchange,
    when the value is obvious - iOS permits the prompt once, and a cold ask
    gets roughly 40% with no second chance.
    """

    model_config = ConfigDict(extra="forbid")

    expo_token: str = Field(min_length=1, max_length=255)
    platform: str = Field(pattern="^(ios|android)$")
    locale: str | None = Field(default=None, max_length=10)
