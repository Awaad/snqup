"""Cards repositories."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.repository import TenantScopedRepository
from acme.domains.cards.enums import TokenKind
from acme.domains.cards.models import Card, CardToken


class CardRepository(TenantScopedRepository[Card]):
    model = Card
    user_column = "user_id"
    organization_column = "organization_id"

    async def count(self) -> int:
        """Live cards for this tenant, used to enforce card.limit.

        Counts through scoped(), so soft-deleted cards do not count against the
        limit - a user who deletes a card must get the slot back.
        """
        stmt = select(func.count()).select_from(self.scoped().subquery())
        return int((await self.session.execute(stmt)).scalar_one())

    async def by_slug(self, slug: str) -> Card | None:
        """Public lookup by slug. NOT tenant-scoped, deliberately.

        The link-in-bio page serves strangers, so this cannot go through
        scoped(). It is the one read here that crosses the tenant boundary, and
        it returns only what a public page shows.
        """
        stmt = select(Card).where(
            Card.slug == slug,
            Card.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def clear_default(self) -> None:
        """Unset the current default before setting a new one.

        `cards_one_default_idx` is a partial unique index, so two defaults are
        rejected by the database. Doing this in the same transaction as the new
        default keeps that from surfacing as a constraint error.
        """
        for card in await self.list(limit=1000):
            if card.is_default:
                card.is_default = False


class CardTokenRepository:
    """Not tenant-scoped: tokens are resolved by a STRANGER holding the token.

    That is the whole point of a token - it is a capability, and the scanner has
    no account. Ownership is checked on the write path (minting, revoking) by
    going through CardRepository first.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, token: str) -> CardToken | None:
        """Find a token by its public value.

        Returns the row even when expired or revoked, so the caller can
        distinguish TOKEN_EXPIRED from TOKEN_REVOKED from TOKEN_NOT_FOUND.
        Collapsing those into one response would leave a user whose badge was
        rotated with no idea why their code stopped working.
        """
        stmt = select(CardToken).where(CardToken.token == token)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def active_static(self, card_id: UUID) -> CardToken | None:
        stmt = select(CardToken).where(
            CardToken.card_id == card_id,
            CardToken.kind == TokenKind.STATIC,
            CardToken.revoked_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def revoke_static(self, card_id: UUID) -> int:
        """Revoke every live static token for a card.

        This is what makes a leaked badge photograph recoverable without
        reprinting five hundred badges (ADR-0002).
        """
        revoked = 0
        stmt = select(CardToken).where(
            CardToken.card_id == card_id,
            CardToken.kind == TokenKind.STATIC,
            CardToken.revoked_at.is_(None),
        )
        for token in (await self._session.execute(stmt)).scalars():
            token.revoked_at = datetime.now(UTC)
            revoked += 1
        return revoked

    def add(self, token: CardToken) -> CardToken:
        self._session.add(token)
        return token
