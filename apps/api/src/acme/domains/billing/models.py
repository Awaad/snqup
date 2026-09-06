"""Billing domain models.

This domain exposes exactly ONE thing to other domains:
entitlements.check(subject, key). Nothing else crosses the boundary, and no
code anywhere asks about a payment provider (ADR-0009).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.core.db import Base, pg_enum
from acme.core.ids import new_id


class Subscription(Base):
    """Raw record from a billing source. Kept for reconciliation and support.

    Authorization never reads this. It reads Entitlement.
    """

    __tablename__ = "subscriptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    subject_kind: Mapped[str] = mapped_column(pg_enum("subject_kind"))
    # KNOWN LIMITATION: polymorphic (user or organization), so no FK is
    # possible and orphans are accepted at the database level. A `principals`
    # supertype table would fix it and was rejected: an orphan is a dead row
    # that grants nobody access, and the cost is two inserts on every signup.
    # See schema/review-2026-09-05.md. Integrity here is the service layer's
    # job, and the reconciliation job must detect orphans.
    subject_id: Mapped[UUID] = mapped_column()
    source: Mapped[str] = mapped_column(pg_enum("entitlement_source"))
    # Apple originalTransactionId / Stripe subscription id.
    source_ref: Mapped[str] = mapped_column(Text)
    plan_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(pg_enum("entitlement_status"))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source", "source_ref"),
        Index("subscriptions_subject_idx", "subject_kind", "subject_id"),
    )


class Entitlement(Base):
    """The ONLY thing authorization ever reads.

    Correct:    if not entitlements.check(subject, "connection.export"): ...
    Rejected:   if not user.stripe_subscription_active: ...

    Adding Google Play later must be one webhook handler and zero changes
    anywhere else. That is the entire point of the design.

    Comped pilot partners are source='manual' with an expires_at, using the
    same table and the same resolver. No special-case code - and the expiry
    matters, because a permanently comped organizer is how the whole meetup
    segment ends up never paying.
    """

    __tablename__ = "entitlements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    subject_kind: Mapped[str] = mapped_column(pg_enum("subject_kind"))
    subject_id: Mapped[UUID] = mapped_column()  # polymorphic, see Subscription
    entitlement_key: Mapped[str] = mapped_column(Text)
    value_int: Mapped[int | None] = mapped_column(Integer)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(pg_enum("entitlement_source"))
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        pg_enum("entitlement_status"), server_default=text("'active'")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # Without this, a row with both NULL is accepted and reads as "entitled
        # to nothing" or "unlimited" depending on the caller. Billing bug in
        # either direction.
        CheckConstraint(
            "(value_int IS NOT NULL)::int + (value_bool IS NOT NULL)::int = 1",
            name="entitlements_one_value",
        ),
        Index(
            "entitlements_active_idx",
            "subject_kind",
            "subject_id",
            "entitlement_key",
            "source",
            unique=True,
            postgresql_where=text("status IN ('active', 'grace')"),
        ),
        Index(
            "entitlements_lookup_idx",
            "subject_kind",
            "subject_id",
            "entitlement_key",
        ),
    )


class BillingEvent(Base):
    """Webhook delivery log.

    Handlers are idempotent against this table. Apple notifications are
    eventually consistent and occasionally duplicated; a handler that assumes
    exactly-once delivery corrupts entitlement state.

    Flow: receive -> verify signature -> insert here (unique on
    source+external_id) -> if already present, return 200 and stop -> process.
    """

    __tablename__ = "billing_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(pg_enum("entitlement_source"))
    external_id: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index(
            "billing_events_unprocessed_idx",
            "received_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )


__all__ = ["BillingEvent", "Entitlement", "Subscription"]
