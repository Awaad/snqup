"""Identity service. The only surface other domains may use."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_id
from acme.domains.identity.enums import OrgRole
from acme.domains.identity.models import (
    DomainVerification,
    Organization,
    OrganizationMember,
    ReservedSlug,
    User,
    UserProfile,
)
from acme.domains.identity.repository import UserRepository


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated caller, as everything downstream sees them.

    A value object, not an ORM model. Passing the ORM User around would let any
    layer lazy-load its way into the profile - and into personal data - from
    places that have no business touching it. It would also leak an identity
    model across a domain boundary, which the import contract forbids.
    """

    id: UUID
    auth_subject: str
    email: str
    locale: str


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)

    async def is_slug_reserved(self, slug: str) -> bool:
        """Route collisions, brand squatting, profanity.

        Exposed as a service method because `reserved_slugs` belongs to this
        domain and other domains may import only `service` (ADR-0025). Cards
        reaching into ReservedSlug directly is exactly the coupling the import
        contract exists to catch - and it did.
        """
        return await self._session.get(ReservedSlug, slug) is not None

    async def current_user(self, auth_subject: str) -> CurrentUser:
        """Map a verified JWT subject to our user.

        Raises rather than returning None: every caller of this needs a user,
        and an Optional here would be checked inconsistently across dozens of
        endpoints.
        """
        found = await self._users.by_auth_subject(auth_subject)
        if found is None:
            raise ApiError(
                "AUTH_ACCOUNT_DISABLED",
                status_code=403,
                message="no active account for this subject",
            )

        user, profile = found
        return CurrentUser(
            id=user.id,
            auth_subject=profile.auth_subject,
            email=profile.email,
            locale=profile.locale,
        )

    async def assert_slug_available(self, slug: str, *, kind: str) -> None:
        """One check for every public slug: cards, organizations, events.

        They share a namespace in the sense that matters - all three end up in
        a URL a stranger reads - so `admin` must be refused whichever surface
        asks for it. Having three copies of this check is how one of them ends
        up out of date.
        """
        normalised = slug.strip().lower()
        if not SLUG_PATTERN.match(normalised):
            raise ApiError(
                "CARD_SLUG_INVALID",
                status_code=422,
                message="slug may contain lowercase letters, digits and hyphens",
            )
        if not SLUG_MIN <= len(normalised) <= SLUG_MAX:
            raise ApiError(
                "CARD_SLUG_INVALID",
                status_code=422,
                message=f"slug must be {SLUG_MIN}-{SLUG_MAX} characters",
            )
        if await self.is_slug_reserved(normalised):
            reserved = await self._session.get(ReservedSlug, normalised)
            raise ApiError(
                "CARD_SLUG_RESERVED",
                status_code=422,
                message="slug is reserved",
                # The reason is user-visible, which is why the reserved list
                # refuses to let one slug sit in two categories.
                details={"reason": reserved.reason if reserved else "reserved", "kind": kind},
            )

    async def create_organization(
        self, *, name: str, slug: str, is_personal: bool = False
    ) -> Organization:
        """Create an organization, validating its slug like any other.

        Organization slugs are as public as card slugs - they appear in event
        URLs - so they go through the same reserved list rather than a second,
        laxer one.
        """
        await self.assert_slug_available(slug, kind="organization")

        taken = await self._session.execute(
            select(Organization).where(Organization.slug == slug, Organization.deleted_at.is_(None))
        )
        if taken.scalar_one_or_none() is not None:
            raise ApiError("ORG_SLUG_TAKEN", status_code=409, message="slug in use")

        org = Organization(name=name, slug=slug, is_personal=is_personal)
        self._session.add(org)
        await self._session.flush()
        return org

    async def personal_organization_for(self, user_id: UUID) -> Organization:
        """The organization a solo organizer acts through (ADR-0027).

        Created lazily on first organizer action rather than at signup: most
        users never organize anything, and a row per consumer signup for a
        feature they never touch is exactly what we rejected.

        Its id is an independent UUIDv7, never derived from the user id -
        entitlements.subject_id is polymorphic with no foreign key, so a shared
        id would make an entitlement ambiguous between user X and organization
        X.
        """
        existing = await self._session.execute(
            select(Organization)
            .join(
                OrganizationMember,
                (OrganizationMember.organization_id == Organization.id)
                & (OrganizationMember.user_id == user_id)
                & (OrganizationMember.deleted_at.is_(None)),
            )
            .where(
                Organization.is_personal.is_(True),
                Organization.deleted_at.is_(None),
            )
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return found

        # Slug derived from the user id, not their name: a display name is not
        # unique, may be absent, and would leak into a public URL.
        org = Organization(
            name="Personal",
            slug=f"p-{new_id().hex[:12]}",
            is_personal=True,
        )
        self._session.add(org)
        await self._session.flush()
        self._session.add(
            OrganizationMember(organization_id=org.id, user_id=user_id, role=OrgRole.OWNER)
        )
        await self._session.flush()
        return org

    async def schedule_deletion(self, user_id: UUID, purge_after: datetime) -> None:
        """Disable now, hard-delete after the grace period.

        Both timestamps are set together: `deleted_at` is what stops the
        account working immediately, and `purge_after` is what the purge job
        reads. Setting only the first would disable the account and never erase
        it, which is the failure mode the purge job exists to prevent.
        """
        user = await self._session.get(User, user_id)
        if user is None:
            raise ApiError("AUTH_ACCOUNT_DISABLED", status_code=403)
        user.deleted_at = datetime.now(UTC)
        user.purge_after = purge_after
        await self._session.flush()

    async def export_profile(self, user_id: UUID) -> dict[str, object]:
        """The profile half of a GDPR export.

        Reads user_profiles, which is where every personal column lives - so a
        column added later is exported automatically, the same property that
        makes erasure a single DELETE.
        """
        profile = await self._session.get(UserProfile, user_id)
        if profile is None:
            return {}
        return {
            "email": profile.email,
            "display_name": profile.display_name,
            "locale": profile.locale,
            "consent_marketing": profile.consent_marketing,
            "consent_transactional": profile.consent_transactional,
            "consent_policy_version": profile.consent_policy_version,
            "birth_year": profile.birth_year,
            "created_at": profile.created_at.isoformat(),
        }

    async def due_for_purge(self, at: datetime, limit: int = 200) -> list[UUID]:
        stmt = (
            select(User.id)
            .where(User.deleted_at.is_not(None))
            .where(User.purge_after.is_not(None))
            .where(User.purge_after <= at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def erase(self, user_id: UUID) -> None:
        """Delete ALL personal data for a user. One statement.

        That is the whole point of the users/user_profiles split (ADR-0027):
        erasure is a single DELETE, so a personal column added later is covered
        by construction rather than by someone remembering to add it to a purge
        list.

        The `users` anchor SURVIVES. Connections reference it with ON DELETE
        RESTRICT, so removing it would either fail or destroy the
        counterparty's record of a meeting that happened.
        """
        await self._session.execute(delete(UserProfile).where(UserProfile.user_id == user_id))
        await self._session.flush()

    async def has_verified_domain(self, organization_id: UUID) -> bool:
        """Whether an organization has proved control of an email domain.

        Exposed as a service method because domain_verifications belongs to
        this domain. Events needs it to gate public indexed pages, and reaching
        into the model directly is exactly the coupling import-linter catches.
        """
        stmt = select(DomainVerification).where(
            DomainVerification.organization_id == organization_id,
            DomainVerification.verified_at.is_not(None),
        )
        return (await self._session.execute(stmt)).first() is not None

    async def provision(
        self, *, auth_subject: str, email: str, display_name: str | None = None
    ) -> CurrentUser:
        """First sign-in: create the anchor and the profile together.

        Two rows in one transaction, never one without the other. An anchor with
        no profile is indistinguishable from an erased account, so a partial
        write here would lock the user out of an account they just created.
        """
        existing = await self._users.by_auth_subject(auth_subject)
        if existing is not None:
            user, profile = existing
            return CurrentUser(
                id=user.id,
                auth_subject=profile.auth_subject,
                email=profile.email,
                locale=profile.locale,
            )

        user = User(id=new_id())
        self._session.add(user)
        profile = UserProfile(
            user_id=user.id,
            auth_subject=auth_subject,
            email=email,
            display_name=display_name,
        )
        self._session.add(profile)
        await self._session.flush()

        return CurrentUser(
            id=user.id,
            auth_subject=auth_subject,
            email=email,
            locale=profile.locale,
        )


# Lowercase, digits, hyphen. No leading or trailing hyphen, no doubles.
# Deliberately narrow: slugs appear in URLs, in QR payloads, and are read aloud.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MIN, SLUG_MAX = 3, 40
