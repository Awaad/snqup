"""Notifications domain models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.core.db import Base


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expo_token: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("expo_token"),
        Index(
            "device_tokens_user_idx",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class Notification(Base):
    """The in-app equivalent of every push.

    Push delivery is best-effort and NOT guaranteed. Every push must have a row
    here, so a missed follow-up reminder still appears in the app rather than
    vanishing. The push is a notification OF a row here, not the thing itself.

    kind: reminder_due | post_event_digest | reciprocity_nudge |
          pending_request | event_announcement
    """

    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "notifications_user_idx",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
    )


__all__ = ["DeviceToken", "Notification"]
