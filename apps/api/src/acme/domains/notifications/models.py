"""Notifications domain models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.core.db import Base, constrained
from acme.core.ids import new_id
from acme.domains.notifications.enums import NotificationKind


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
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

    """

    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[NotificationKind] = mapped_column(constrained(NotificationKind))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per channel: push and email fail independently and for different
    # reasons, and retrying both because one failed would double-send.
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Without a ceiling a permanently failing notification retries forever and
    # buries the ones that could still succeed.
    delivery_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    delivery_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "notifications_user_idx",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
        # The delivery worker's queue. Declared here as well as in the schema:
        # an index the database has and no model knows about is one that
        # `alembic autogenerate` will try to DROP.
        Index(
            "notifications_undelivered_idx",
            "created_at",
            postgresql_where=text(
                "pushed_at IS NULL AND emailed_at IS NULL AND delivery_attempts < 5"
            ),
        ),
    )


__all__ = ["DeviceToken", "Notification"]
