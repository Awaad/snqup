"""Identity service. The only surface other domains may use."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_id
from acme.domains.identity.enums import OrgRole

if TYPE_CHECKING:
    from acme.domains.identity.schemas import ProfileOut, ProfileUpdate
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

    async def profile(self, user_id: UUID) -> "ProfileOut":
        from acme.domains.identity.schemas import ProfileOut

        row = await self._session.get(UserProfile, user_id)
        if row is None:
            raise ApiError("AUTH_ACCOUNT_DISABLED", status_code=403)
        return ProfileOut.model_validate(row)

    async def update_profile(self, user_id: UUID, payload: "ProfileUpdate") -> "ProfileOut":
        from acme.domains.identity.schemas import ProfileOut

        row = await self._session.get(UserProfile, user_id)
        if row is None:
            raise ApiError("AUTH_ACCOUNT_DISABLED", status_code=403)

        # exclude_unset, so an absent field is left alone rather than blanked.
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await self._session.flush()
        return ProfileOut.model_validate(row)

    async def resolve_or_provision(
        self, *, auth_subject: str, email: str | None, display_name: str | None
    ) -> CurrentUser:
        """Map a verified JWT to our user, creating one on FIRST sign-in.

        This was the gap that made the product unusable: `provision()` existed
        and nothing called it, so a brand-new Supabase user with a perfectly
        valid token received AUTH_ACCOUNT_DISABLED. Nobody could sign up.

        Provisioning here rather than at a /signup endpoint is deliberate.
        Supabase has already authenticated the person; requiring a second,
        separate call to create the account adds a step that can fail on its
        own and leaves a verified user with no row when it does.

        THE DISTINCTION THAT MATTERS: no row at all means a new user and we
        create one. A row with `deleted_at` set means an ERASED account, and
        provisioning would silently resurrect someone who asked to be deleted -
        with a fresh empty profile carrying their old id. That is refused.
        """
        found = await self._users.by_auth_subject(auth_subject)
        if found is not None:
            user, profile = found
            return CurrentUser(
                id=user.id,
                auth_subject=profile.auth_subject,
                email=profile.email,
                locale=profile.locale,
            )

        # Erased, not new. by_auth_subject filters deleted accounts, so this
        # second look is what tells them apart.
        if await self._was_erased(auth_subject):
            raise ApiError(
                "AUTH_ACCOUNT_DISABLED",
                status_code=403,
                message="this account was deleted",
            )

        if not email:
            # Supabase issues tokens for phone and anonymous sign-in too, and
            # every downstream feature - digests, CRM sync, account recovery -
            # assumes an email. Failing here beats a half-usable account.
            raise ApiError(
                "AUTH_TOKEN_INVALID",
                status_code=401,
                message="token carries no email; cannot provision an account",
            )

        return await self.provision(
            auth_subject=auth_subject, email=email, display_name=display_name
        )

    async def _was_erased(self, auth_subject: str) -> bool:
        """Whether this subject belonged to an account that has been erased.

        After a purge the profile row is gone, so there is nothing to match on
        - which is correct: a purged account genuinely has no record, and the
        person may legitimately sign up again. This catches the window between
        deletion and purge, which is 30 days.
        """
        stmt = (
            select(User)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(UserProfile.auth_subject == auth_subject)
            .where(User.deleted_at.is_not(None))
        )
        return (await self._session.execute(stmt)).first() is not None

    async def current_user(self, auth_subject: str) -> CurrentUser:
        """Resolve an EXISTING user. Does not provision.

        Kept separate from resolve_or_provision because the admin surface and
        the job runner need to look a user up without the side effect of
        creating one.
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

    async def notification_profile(self, user_id: UUID) -> dict[str, object]:
        """Everything delivery needs about a recipient, in one read.

        Returns an empty dict for an erased user: their profile row is gone but
        `notifications` may still hold rows, and delivering to a deleted account
        would be both useless and a disclosure.
        """
        profile = await self._session.get(UserProfile, user_id)
        if profile is None:
            return {}
        return {
            "email": profile.email,
            "timezone": profile.timezone,
            "notification_prefs": dict(profile.notification_prefs),
            "consent_transactional": profile.consent_transactional,
        }

    async def set_notification_prefs(
        self, user_id: UUID, preferences: dict[str, dict[str, bool]]
    ) -> dict[str, dict[str, bool]]:
        profile = await self._session.get(UserProfile, user_id)
        if profile is None:
            raise ApiError("AUTH_ACCOUNT_DISABLED", status_code=403)
        profile.notification_prefs = preferences
        await self._session.flush()
        return preferences

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

    async def organization_branding(self, organization_id: UUID) -> dict[str, str | None] | None:
        """Just what a public page renders.

        Deliberately narrow: a public event page needs a name and a logo, and
        returning the whole organization would put its description, website and
        internal flags on a page anyone can read.
        """
        org = await self._session.get(Organization, organization_id)
        if org is None or org.deleted_at is not None:
            return None
        return {"name": org.name, "logo_path": org.logo_path}

    async def admin_search(self, *, email: str | None, limit: int = 20) -> list[dict[str, object]]:
        """Support lookup. Emails MASKED in the result.

        Staff browsing a contact database is exactly the risk this product
        carries, so a list view identifies an account and no more. Full details
        need a single-record view, which is audited separately.
        """
        stmt = select(User, UserProfile).join(
            UserProfile, UserProfile.user_id == User.id, isouter=True
        )
        if email:
            stmt = stmt.where(UserProfile.email == email.strip().lower())
        rows = (await self._session.execute(stmt.limit(limit))).all()

        results: list[dict[str, object]] = []
        for user, profile in rows:
            results.append(
                {
                    "id": user.id,
                    "email_masked": _mask_email(profile.email) if profile else "erased",
                    "display_name": profile.display_name if profile else None,
                    "deleted_at": (user.deleted_at.isoformat() if user.deleted_at else None),
                    "purge_after": (user.purge_after.isoformat() if user.purge_after else None),
                }
            )
        return results

    async def existing_subject_ids(self, candidates: set[UUID]) -> set[UUID]:
        """Which of these ids are a real user or organization.

        Used by the reconciliation job to find orphaned entitlements, which the
        database cannot catch because subject_id is polymorphic.
        """
        if not candidates:
            return set()
        users = set(
            (await self._session.execute(select(User.id).where(User.id.in_(candidates)))).scalars()
        )
        orgs = set(
            (
                await self._session.execute(
                    select(Organization.id).where(Organization.id.in_(candidates))
                )
            ).scalars()
        )
        return users | orgs

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


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}"
