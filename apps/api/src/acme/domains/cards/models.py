"""Cards domain models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from acme.core.db import Base, CIText, pg_enum


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Declared by table name string, never by importing identity.models.
    # A string carries no import, so the domain boundary holds (ADR-0025).
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(pg_enum("card_kind"), server_default=text("'personal'"))
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    display_name: Mapped[str] = mapped_column(Text)
    headline: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(CIText())
    phone: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    # Object storage key, not a URL. URLs are signed at read time so they can
    # expire; storing one would bake in a host and an expiry.
    photo_path: Mapped[str | None] = mapped_column(Text)
    socials: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    custom_fields: Mapped[list[object]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    # USER DATA, versioned. NOT the app design system (ADR-0017). A snapshot
    # taken six months ago must still render with the theme it had, so the
    # renderer handles old theme_version values rather than migrating them.
    theme: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    theme_version: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    # Paid. Contrast is validated at write time: a pale-on-white code scans
    # badly and arrives as "scanning is broken" in support.
    qr_style: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    slug: Mapped[str | None] = mapped_column(CIText())

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tokens: Mapped[list["CardToken"]] = relationship(back_populates="card")

    __table_args__ = (
        Index(
            "cards_slug_idx",
            "slug",
            unique=True,
            postgresql_where=text("slug IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "cards_one_default_idx",
            "user_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
        Index("cards_user_idx", "user_id", postgresql_where=text("deleted_at IS NULL")),
    )


class CardToken(Base):
    """Live and static tokens (ADR-0002).

    The two behave differently and the difference is the whole security model:

      live    rendered in-app only, 10-15 min TTL, produces a SYMMETRIC
              exchange. The owner had to open the app and present it, and that
              act is the consent.

      static  printable, exportable, written to NFC, on the link page.
              ONE-WAY only. The scanner receives the card; the owner receives a
              pending request. Killed by revoked_at rather than expiry, which
              is what makes a leaked badge photo fixable without reprinting
              five hundred badges.

    Never make a static token produce an automatic exchange. That is the
    harvesting vector the whole design exists to close.

    `token` is opaque, ~22 chars, NOT a UUID: a v7 would leak its creation time
    and 36 characters makes a denser, less reliably scannable code (ADR-0010).
    """

    __tablename__ = "card_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    card_id: Mapped[UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(pg_enum("token_kind"))
    token: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    card: Mapped[Card] = relationship(back_populates="tokens")

    __table_args__ = (
        CheckConstraint("kind <> 'live' OR expires_at IS NOT NULL", name="live_tokens_expire"),
        Index(
            "card_tokens_card_idx",
            "card_id",
            "kind",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "card_tokens_expiry_idx",
            "expires_at",
            postgresql_where=text("expires_at IS NOT NULL"),
        ),
    )


__all__ = ["Card", "CardToken"]
