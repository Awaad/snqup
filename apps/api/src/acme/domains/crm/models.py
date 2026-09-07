"""CRM domain models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.core.db import Base, constrained
from acme.core.ids import new_id
from acme.domains.crm.enums import CrmProvider


class CrmConnection(Base):
    __tablename__ = "crm_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    provider: Mapped[CrmProvider] = mapped_column(constrained(CrmProvider))

    # Encrypted at rest by the application, never plaintext. A CRM token is a
    # write credential into someone's customer database - the blast radius is
    # their business, not just their account here.
    credentials: Mapped[bytes] = mapped_column(LargeBinary)

    # Per CONNECTION, not per provider: two HubSpot portals name their custom
    # properties differently, and hardcoding one writes into the wrong field.
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    # A CRM that silently stops syncing is worse than one never connected,
    # because the user believes their contacts are safe. These three are what
    # let the app say "reconnect" instead of failing quietly.
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    needs_reauth: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL) <> (organization_id IS NOT NULL)",
            name="crm_connections_subject",
        ),
        Index(
            "crm_connections_user_idx",
            "user_id",
            "provider",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "crm_connections_org_idx",
            "organization_id",
            "provider",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


class CrmSyncedContact(Base):
    """What was pushed where.

    The external id is what makes a retry safe: without it, every retry creates
    a duplicate contact in someone's CRM - the failure users complain about
    loudest and cannot easily undo.
    """

    __tablename__ = "crm_synced_contacts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    crm_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("crm_connections.id", ondelete="CASCADE")
    )
    connection_view_id: Mapped[UUID] = mapped_column(
        ForeignKey("connection_views.id", ondelete="CASCADE")
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "crm_synced_contacts_unique_idx",
            "crm_connection_id",
            "connection_view_id",
            unique=True,
        ),
    )
