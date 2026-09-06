"""Connections domain models.

The split between Connection and ConnectionView is the most important
structural decision in this schema (ADR-0003). Read the class docstrings before
changing either.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.core.db import Base, CIText, pg_enum
from acme.core.ids import new_id


class Connection(Base):
    """The immutable edge. Written once at exchange.

    NEVER carries per-user private data. Notes live on ConnectionView, in a
    different row with a different owner. That is the structural guarantee that
    a query or serialization bug cannot leak A's note about B *to* B - and
    notes will contain things like "seemed unprepared, low priority".

    Two details that look like they could be simplified and cannot:

    1. user_low_id < user_high_id is enforced by CHECK. Without it the same
       pair is storable twice in opposite order and the unique indexes below do
       nothing. Application code must order the pair before insert.

    2. TWO partial unique indexes, not one constraint. Postgres treats NULLs as
       distinct in unique indexes, so a single constraint over a nullable
       event_id would permit UNLIMITED duplicates for non-event connections.
       tests/test_schema_invariants.py guards this specifically because a
       future refactor will try to collapse them.
    """

    __tablename__ = "connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)

    # RESTRICT, not CASCADE. The identity anchor must survive as long as any
    # connection references it: erasing A must not destroy B's record of the
    # meeting or B's private note about it (ADR-0020).
    user_low_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    user_high_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    card_low_id: Mapped[UUID | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    card_high_id: Mapped[UUID | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))

    # Immutable record of what was ACTUALLY exchanged (ADR-0004). Survives card
    # edits, card deletion, and erasure of the counterpart. Without it, B could
    # present as "Engineer at Acme", exchange with 200 people, then rewrite to
    # "Recruiter at Competitor" and retroactively change what everyone received.
    card_low_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    card_high_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    snapshot_version: Mapped[int] = mapped_column(server_default=text("1"))

    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    # Recorded from v1. Retrofitting loses channel attribution history, which
    # is how we learn whether NFC or QR actually gets used.
    channel: Mapped[str] = mapped_column(pg_enum("scan_channel"))
    state: Mapped[str] = mapped_column(
        pg_enum("connection_state"), server_default=text("'confirmed'")
    )

    # UNUSED IN v1. Discovery needs to know which edges may contribute to
    # mutual-connection counts (ADR-0023). Do not remove as dead code.
    visibility: Mapped[str] = mapped_column(
        pg_enum("connection_visibility"), server_default=text("'private'")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("user_low_id < user_high_id", name="connections_ordered"),
        Index(
            "connections_pair_event_idx",
            "user_low_id",
            "user_high_id",
            "event_id",
            unique=True,
            postgresql_where=text("event_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "connections_pair_noevent_idx",
            "user_low_id",
            "user_high_id",
            unique=True,
            postgresql_where=text("event_id IS NULL AND deleted_at IS NULL"),
        ),
        Index(
            "connections_event_idx",
            "event_id",
            "created_at",
            postgresql_where=text("event_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


class ConnectionView(Base):
    """One row per participant. This is where private data lives.

    Each user owns their own row and can never read the other's. Deletion is
    per-view: the edge survives until both views are deleted, then the purge
    job removes it. Both users expect asymmetric deletion and GDPR requires it.

    The admin console cannot read `note`. If an investigation genuinely needs
    it, that is a legal process, not a UI feature.
    """

    __tablename__ = "connection_views"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    note: Mapped[str | None] = mapped_column(Text)
    # Field-level merge with a visible marker, never silent overwrite
    # (ADR-0016). Notes are the highest-value user-authored data in the
    # product; visible complexity beats invisible data loss.
    note_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note_conflict: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Meeting the same person at three events is one contact with three event
    # tags, not three contacts. Dedup happens here, not by refusing the edge.
    merged_into_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connection_views.id", ondelete="SET NULL")
    )

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # UniqueConstraint, not Index: the schema declares it as a table
        # constraint, and Postgres names the backing index
        # connection_views_connection_id_user_id_key. Declaring it as a named
        # Index instead makes autogenerate want to create a second one.
        UniqueConstraint("connection_id", "user_id"),
        # The connection list screen's access pattern.
        Index(
            "connection_views_user_idx",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL AND archived_at IS NULL"),
        ),
        Index(
            "connection_views_reminder_idx",
            "reminder_at",
            postgresql_where=text(
                "reminder_at IS NOT NULL AND reminder_done_at IS NULL AND deleted_at IS NULL"
            ),
        ),
    )


class AnonymousScan(Base):
    """A scan by someone with no account. The MAJORITY path.

    Most scanners will not have the app. Counted so organizers can report on it
    and so the fallback page's conversion is measurable - it is the growth loop,
    not an edge case.

    The optional reply form creates a pending exchange and an invitation to
    claim, which is how a non-user becomes a user.
    """

    __tablename__ = "anonymous_scans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    card_id: Mapped[UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    token_id: Mapped[UUID | None] = mapped_column(ForeignKey("card_tokens.id", ondelete="SET NULL"))
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(pg_enum("scan_channel"))
    saved_vcard: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    added_wallet: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    reply_email: Mapped[str | None] = mapped_column(CIText())
    reply_name: Mapped[str | None] = mapped_column(Text)
    reply_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    claimed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Truncated. NEVER store a raw IP: deriving geography from it is a
    # processing activity requiring a lawful basis and disclosure (ADR-0012).
    ip_prefix: Mapped[str | None] = mapped_column(INET)
    country: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("anonymous_scans_card_idx", "card_id", text("created_at DESC")),
        Index(
            "anonymous_scans_event_idx",
            "event_id",
            postgresql_where=text("event_id IS NOT NULL"),
        ),
    )


__all__ = ["AnonymousScan", "Connection", "ConnectionView"]
