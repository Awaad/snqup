"""Identity repositories."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.identity.models import User, UserProfile


class UserRepository:
    """Not tenant-scoped: this is what RESOLVES the tenant.

    Every other repository takes a Tenant in its constructor. This one runs
    before there is a tenant, so it cannot inherit that base. It is deliberately
    tiny for that reason - the less that lives above the tenant boundary, the
    smaller the surface where a filter can be forgotten.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_auth_subject(self, auth_subject: str) -> tuple[User, UserProfile] | None:
        """Resolve an IdP subject to our user.

        Joins to the profile because a user without one has been erased: the
        anchor survives so counterparties keep their records, but there is no
        account to act as (ADR-0020). Returning None here is what makes an
        erased user's token stop working.
        """
        stmt = (
            select(User, UserProfile)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(UserProfile.auth_subject == auth_subject)
            .where(User.deleted_at.is_(None))
        )
        row = (await self._session.execute(stmt)).one_or_none()
        return (row[0], row[1]) if row else None

    async def by_id(self, user_id: UUID) -> User | None:
        stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        return (await self._session.execute(stmt)).scalar_one_or_none()
