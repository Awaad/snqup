"""Identity domain models.

Nothing outside this domain may import from here (ADR-0025).
"""

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

from acme.core.db import Base, CIText, constrained
from acme.core.ids import new_id
from acme.domains.identity.enums import OrgRole


class User(Base):
    """Identity anchor. Holds NO personal data, ever.

    Exists so connections, audit entries and moderation history can reference a
    participant without that reference keeping personal data alive.

    Erasure deletes the UserProfile row, not this one. The foreign keys from
    connections are ON DELETE RESTRICT, so this row cannot be removed while a
    connection references it - which is what stops one user's erasure from
    destroying another user's record of a meeting (ADR-0020).

    If you are about to add a personal column here: it belongs on UserProfile.
    tests/test_schema_invariants.py asserts this table's exact column set and
    will fail.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", uselist=False)

    __table_args__ = (
        Index(
            "users_purge_idx",
            "purge_after",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )


class UserProfile(Base):
    """ALL personal data about a user.

    Erasure is a single DELETE of this row. That is the point of the split: the
    alternative (anonymising the users row column by column) depends on a purge
    job nulling every personal column, so adding one later and forgetting the
    list is a silent compliance defect with no test that catches it.

    A new personal column added here is covered by erasure automatically.
    """

    __tablename__ = "user_profiles"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    auth_subject: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(CIText())
    display_name: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(Text, server_default=text("'en'"))

    # Separate from transactional on purpose. GDPR requires opt-in for
    # marketing, and retrofitting the distinction later is unpleasant.
    consent_transactional: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    consent_marketing: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    consent_policy_version: Mapped[str | None] = mapped_column(Text)

    birth_year: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="profile")

    __table_args__ = (Index("user_profiles_email_idx", "email", unique=True),)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(CIText())
    # Functional, so columns rather than keys in `brand`: website seeds
    # domain_verifications, which gates public indexed event pages and the
    # verified badge. Anything queried or acted on gets a column; `brand` is
    # presentation only, or it becomes a junk drawer nobody can query.
    website: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    logo_path: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # A solo organizer's personal organization, created at organizer signup so
    # events.organization_id can be NOT NULL. Its id is an independent UUIDv7,
    # never derived from the user id: entitlements.subject_id is polymorphic
    # with no FK, so a shared id would make an entitlement ambiguous between
    # user X and organization X (ADR-0027).
    is_personal: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Partial: a soft-deleted organization must not hold its slug forever.
        Index(
            "organizations_slug_idx",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class OrganizationMember(Base):
    """Surrogate PK, not (organization_id, user_id).

    With a natural key, a member who leaves and is later re-invited collides
    with their own soft-deleted row and the invite fails.
    """

    __tablename__ = "organization_members"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[OrgRole] = mapped_column(constrained(OrgRole))
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "org_members_active_idx",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "org_members_user_idx",
            "user_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class DomainVerification(Base):
    """Proof of control over an email domain.

    This is the verification mechanism, deliberately NOT KYC. Storing
    government ID is a compliance burden and a breach risk that dwarfs
    everything else in this product, to solve a problem we do not have.
    """

    __tablename__ = "domain_verifications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(CIText())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(organization_id IS NOT NULL) <> (user_id IS NOT NULL)",
            name="domain_verification_subject",
        ),
        Index(
            "domain_verifications_unique_idx",
            "domain",
            text("COALESCE(organization_id, user_id)"),
            unique=True,
        ),
    )


class ReservedSlug(Base):
    """Route collisions, brand squatting, profanity.

    Seeded by migration so every environment has it. Add to it proactively: a
    brand added after someone registers it is a dispute; added before, nothing.
    """

    __tablename__ = "reserved_slugs"

    slug: Mapped[str] = mapped_column(CIText(), primary_key=True)
    reason: Mapped[str] = mapped_column(Text)


__all__ = [
    "DomainVerification",
    "Organization",
    "OrganizationMember",
    "ReservedSlug",
    "User",
    "UserProfile",
]
