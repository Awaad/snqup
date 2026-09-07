"""Identity service. The only surface other domains may use."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_id
from acme.domains.identity.models import (
    DomainVerification,
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
