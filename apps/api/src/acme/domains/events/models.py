"""Events domain models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.core.db import Base, CIText, pg_enum


class Event(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Nullable: solo organizers exist and must not need a shell org (ADR-0018).
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))

    name: Mapped[str] = mapped_column(Text)
    # Tiptap JSON, sanitized on write AND on read. Never raw HTML: user HTML on
    # the public domain is the highest-consequence vulnerability here (ADR-0019).
    description: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    banner_path: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str] = mapped_column(CIText())
    slug: Mapped[str | None] = mapped_column(CIText())
    visibility: Mapped[str] = mapped_column(
        pg_enum("event_visibility"), server_default=text("'unlisted'")
    )

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # IANA name, e.g. 'Europe/Berlin'. NOT an offset: peak-activity analytics
    # are meaningless without it and DST makes offsets wrong twice a year.
    # The post-event digest also fires 24h after ends_at in LOCAL time.
    timezone: Mapped[str] = mapped_column(Text)

    # Off by default, organizer-enabled, per-attendee opt-out. Gamifying scan
    # counts produces farmed, worthless connections.
    leaderboard_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="events_time_order"),
        # Partial, so a soft-deleted event does not hold its join code hostage.
        Index(
            "events_code_idx",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "events_slug_idx",
            "slug",
            unique=True,
            postgresql_where=text("slug IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "events_org_idx",
            "organization_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "events_public_idx",
            text("starts_at DESC"),
            postgresql_where=text("visibility = 'public' AND deleted_at IS NULL"),
        ),
    )


class EventStaff(Base):
    """Separate from organization membership (ADR-0018).

    A scanner hired for one day must see one event and nothing else. "Can edit
    the organization's brand" and "can view this event's dashboard" are
    different powers and neither implies the other.
    """

    __tablename__ = "event_staff"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(pg_enum("event_staff_role"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "event_staff_active_idx",
            "event_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class EventAttendee(Base):
    """Surrogate PK so leaving and rejoining works.

    With a natural key, an attendee who left by accident could never rejoin,
    and it presents as a support ticket rather than a crash.
    """

    __tablename__ = "event_attendees"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    card_id: Mapped[UUID | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    leaderboard_opt_out: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    # UNUSED IN v1. Event-scoped discovery ships in v1.1 (ADR-0023). Present so
    # that release needs no migration. Do not remove as dead code.
    discoverable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovery_prefs: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "event_attendees_active_idx",
            "event_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "event_attendees_user_idx",
            "user_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class EventExportConsent(Base):
    """Per-event consent to appear in an organizer's attendee export.

    "Only with consent" is not a specification (ADR-0012). This records consent
    from whom, in what wording, under which policy version, and when.

    We cannot claw back an already-downloaded CSV. That is stated in the
    consent copy and the organizer terms, and is a documented limitation rather
    than a gap. Revocation removes the attendee from FUTURE exports.
    """

    __tablename__ = "event_export_consents"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(Text)
    consent_text: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(Text)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "event_export_consents_unique_idx",
            "event_id",
            "user_id",
            "scope",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class EventRosterEntry(Base):
    """Organizer-uploaded registration list.

    Lets the dashboard report against EXPECTED attendance rather than only
    actuals, which is what makes it saleable: "78% of your attendees made a
    connection" needs a denominator.

    Unique on (event_id, email) because organizers re-upload lists, and a
    silent duplicate doubles that denominator.
    """

    __tablename__ = "event_roster_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    email: Mapped[str | None] = mapped_column(CIText())
    display_name: Mapped[str | None] = mapped_column(Text)
    matched_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("event_roster_event_idx", "event_id"),
        Index(
            "event_roster_email_idx",
            "event_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )


__all__ = [
    "Event",
    "EventAttendee",
    "EventExportConsent",
    "EventRosterEntry",
    "EventStaff",
]
