"""Tenant isolation.

ADR-0018 says forgetting a tenant filter must be structurally impossible, not
merely discouraged. We declined Postgres RLS (ADR-0005), so this file is the
evidence that the code-level substitute actually holds.

If any test here fails, treat it as a security incident in the making rather
than a broken test.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.ids import new_id
from acme.core.repository import Tenant, TenantScopedRepository
from acme.domains.cards.models import Card
from acme.domains.identity.models import User, UserProfile

pytestmark = pytest.mark.integration


class CardRepository(TenantScopedRepository[Card]):
    model = Card
    user_column = "user_id"
    organization_column = "organization_id"


async def _user(session: AsyncSession, email: str) -> User:
    user = User()
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, auth_subject=f"sub-{email}", email=email))
    await session.flush()
    return user


async def _card(session: AsyncSession, user: User, name: str) -> Card:
    card = Card(user_id=user.id, display_name=name)
    session.add(card)
    await session.flush()
    return card


class TestCrossTenantReadsAreImpossible:
    async def test_list_returns_only_the_callers_rows(self, session: AsyncSession) -> None:
        alice = await _user(session, "alice@example.com")
        bob = await _user(session, "bob@example.com")
        await _card(session, alice, "Alice card")
        await _card(session, bob, "Bob card")

        repo = CardRepository(session, Tenant.user(alice.id))
        names = {c.display_name for c in await repo.list()}

        assert names == {"Alice card"}

    async def test_get_by_id_refuses_another_tenants_row(self, session: AsyncSession) -> None:
        """The id is known and correct. It must still return nothing.

        Returning None rather than raising is deliberate: a distinct response
        for "exists but not yours" confirms the row exists to someone who
        should not know that.
        """
        alice = await _user(session, "alice2@example.com")
        bob = await _user(session, "bob2@example.com")
        bobs_card = await _card(session, bob, "Bob card")

        repo = CardRepository(session, Tenant.user(alice.id))
        assert await repo.get(bobs_card.id) is None

        # ...and the row genuinely exists, so this is not a false pass.
        assert await CardRepository(session, Tenant.user(bob.id)).get(bobs_card.id)

    async def test_soft_deleted_rows_are_excluded_by_default(self, session: AsyncSession) -> None:
        """Soft delete is on every user-facing table (ADR-0020).

        Remembering the filter on every query is the same class of mistake as
        forgetting the tenant, so the base handles both.
        """
        from datetime import UTC, datetime

        alice = await _user(session, "alice3@example.com")
        card = await _card(session, alice, "Deleted card")
        card.deleted_at = datetime.now(UTC)
        await session.flush()

        repo = CardRepository(session, Tenant.user(alice.id))
        assert await repo.list() == []
        assert await repo.get(card.id) is None

        # The purge job and restore-within-grace need the other behaviour.
        stmt = repo.scoped(include_deleted=True)
        rows = list((await session.execute(stmt)).scalars())
        assert len(rows) == 1


class TestCrossTenantWritesAreRefused:
    async def test_writing_another_tenants_row_raises(self, session: AsyncSession) -> None:
        """The attack a read-side filter cannot catch.

        A service could construct an entity carrying someone else's tenant id.
        No read filter would ever notice: the row would simply be invisible to
        its actual owner and visible to whoever wrote it.
        """
        alice = await _user(session, "alice4@example.com")
        bob = await _user(session, "bob4@example.com")

        repo = CardRepository(session, Tenant.user(alice.id))
        smuggled = Card(user_id=bob.id, display_name="not mine")

        with pytest.raises(PermissionError):
            repo.add(smuggled)

    async def test_tenant_is_filled_in_when_omitted(self, session: AsyncSession) -> None:
        alice = await _user(session, "alice5@example.com")
        repo = CardRepository(session, Tenant.user(alice.id))

        card = repo.add(Card(display_name="mine"))
        await session.flush()

        assert card.user_id == alice.id


class TestTenantKindsAreNotInterchangeable:
    """A user tenant and an organization tenant are different powers.

    "Can edit the organization's brand" and "can see my own cards" must never
    resolve through the same predicate by accident.
    """

    async def test_organization_tenant_filters_on_the_organization_column(
        self, session: AsyncSession
    ) -> None:
        alice = await _user(session, "alice6@example.com")
        await _card(session, alice, "Personal card")

        org_id = new_id()
        repo = CardRepository(session, Tenant.organization(org_id))

        # Alice's personal card has organization_id NULL, so an org tenant
        # must not see it.
        assert await repo.list() == []

    async def test_repository_without_a_column_refuses_that_tenant_kind(
        self, session: AsyncSession
    ) -> None:
        """Fail loudly rather than silently returning everything or nothing."""

        class UserOnlyRepository(TenantScopedRepository[Card]):
            model = Card
            user_column = "user_id"
            organization_column = None

        repo = UserOnlyRepository(session, Tenant.organization(new_id()))
        with pytest.raises(TypeError, match="no organization_column"):
            repo.scoped()
