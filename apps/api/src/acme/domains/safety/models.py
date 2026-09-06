"""Safety and compliance domain models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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

from acme.core.db import Base, constrained
from acme.core.ids import new_id
from acme.domains.safety.enums import ReportStatus


class Report(Base):
    """Abuse report.

    Subject FKs are SET NULL, not CASCADE. Suspending a phishing card and then
    purging it must not erase the record of WHY it was suspended: repeat-
    offender detection and any appeal both depend on that history surviving.

    subject_label is denormalised for the same reason - the report has to stay
    readable after the subject is gone.
    """

    __tablename__ = "reports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    reporter_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    subject_card_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL")
    )
    subject_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    subject_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL")
    )
    subject_label: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(constrained(ReportStatus), server_default=text("'open'"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("reports_open_idx", "created_at", postgresql_where=text("status = 'open'")),
    )


class Block(Base):
    """Prevents exchange in both directions.

    Blocks are private: never reveal to the blocked party that they were
    blocked. The scan endpoint returns SCAN_BLOCKED to the scanner without
    saying who blocked whom.
    """

    __tablename__ = "blocks"

    blocker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    blocked_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("blocker_user_id <> blocked_user_id", name="blocks_not_self"),
    )


class AuditLog(Base):
    """Append-only. Every cross-tenant access, admin action and data export.

    The admin console is this table's primary consumer: every action it takes
    writes here, no exceptions. actor is SET NULL so the audit trail survives
    the actor's own erasure.
    """

    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text)
    subject_kind: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[UUID | None] = mapped_column()
    audit_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("audit_log_actor_idx", "actor_user_id", text("created_at DESC")),
        Index(
            "audit_log_subject_idx",
            "subject_kind",
            "subject_id",
            text("created_at DESC"),
        ),
    )


class DataExportRequest(Base):
    """GDPR Article 20. ALWAYS FREE.

    Distinct from the paid workflow export (ADR-0020). Different table,
    different entry point, different copy, and CONNECTION_EXPORT_NOT_ENTITLED
    must never be returned for this path - gating the paid export must not read
    as gating a legal right.
    """

    __tablename__ = "data_export_requests"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_path: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["AuditLog", "Block", "DataExportRequest", "Report"]
