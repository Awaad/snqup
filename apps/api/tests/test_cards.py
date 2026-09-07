"""Cards and tokens.

The token tests are the important ones. TokenKind is the security model of the
whole product (ADR-0002): a live token produces a symmetric exchange because
presenting it in-app IS the consent, and a static token never does because it
can be photographed off a badge without the owner knowing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.repository import Tenant
from acme.domains.billing.enums import EntitlementSource, SubjectKind
from acme.domains.billing.models import Entitlement
from acme.domains.cards.enums import TokenKind
from acme.domains.cards.models import CardToken
from acme.domains.cards.schemas import CardCreate, CardUpdate
from acme.domains.cards.service import CardsService, TokenResolver, contrast_ratio
from acme.domains.identity.models import User, UserProfile

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession, email: str) -> User:
    user = User()
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, auth_subject=f"s-{email}", email=email))
    await session.flush()
    return user


async def _grant(
    session: AsyncSession,
    user: User,
    key: str,
    *,
    value: int | bool,
    source: EntitlementSource = EntitlementSource.MANUAL,
) -> None:
    """Grant one entitlement.

    `source` matters. entitlements_active_idx is unique on
    (subject_kind, subject_id, entitlement_key, source) for active rows, so a
    subject holds at most ONE active entitlement per source. Multiple rows for
    one key therefore always mean multiple sources - which is the real case:
    someone subscribing on the web, forgetting, and subscribing again in the
    app (ADR-0009).
    """
    session.add(
        Entitlement(
            subject_kind=SubjectKind.USER,
            subject_id=user.id,
            entitlement_key=key,
            value_int=(value if isinstance(value, int) and not isinstance(value, bool) else None),
            value_bool=value if isinstance(value, bool) else None,
            source=source,
        )
    )
    await session.flush()


def _service(session: AsyncSession, user: User) -> CardsService:
    return CardsService(session, Tenant.user(user.id))


class TestCardLimits:
    async def test_free_tier_allows_one_card(self, session: AsyncSession) -> None:
        user = await _user(session, "limit@example.com")
        service = _service(session, user)
        await service.create(CardCreate(display_name="First"))

        with pytest.raises(ApiError) as exc:
            await service.create(CardCreate(display_name="Second"))
        assert exc.value.code == "CARD_LIMIT_REACHED"

    async def test_entitlement_raises_the_limit(self, session: AsyncSession) -> None:
        user = await _user(session, "paid@example.com")
        await _grant(session, user, "card.limit", value=5)
        service = _service(session, user)

        for i in range(5):
            await service.create(CardCreate(display_name=f"Card {i}"))

        with pytest.raises(ApiError):
            await service.create(CardCreate(display_name="Sixth"))

    async def test_deleting_a_card_frees_the_slot(self, session: AsyncSession) -> None:
        """Soft-deleted cards must not count against the limit.

        Otherwise a free user who deletes their only card can never make
        another, which reads as the product being broken.
        """
        user = await _user(session, "freed@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="First"))
        await service.delete(card.id)

        await service.create(CardCreate(display_name="Replacement"))


class TestDefaultCard:
    async def test_first_card_is_default_even_when_not_requested(
        self, session: AsyncSession
    ) -> None:
        """A user with cards but no default has no card to present."""
        user = await _user(session, "default@example.com")
        card = await _service(session, user).create(
            CardCreate(display_name="Only", is_default=False)
        )
        assert card.is_default is True

    async def test_setting_a_new_default_clears_the_old_one(self, session: AsyncSession) -> None:
        """cards_one_default_idx is a partial unique index, so two defaults are
        a database error. Clearing in the same transaction keeps that from
        surfacing as a constraint violation."""
        user = await _user(session, "default2@example.com")
        await _grant(session, user, "card.limit", value=5)
        service = _service(session, user)

        first = await service.create(CardCreate(display_name="First"))
        second = await service.create(CardCreate(display_name="Second"))
        await service.update(second.id, CardUpdate(is_default=True))

        assert second.is_default is True
        assert first.is_default is False


class TestSlugs:
    async def test_reserved_slug_is_refused(self, session: AsyncSession) -> None:
        """`admin` is seeded by migration 0002, not by this test.

        Seeding in the migration is what makes local and staging behave like
        production - otherwise `/u/admin` is issuable everywhere except the one
        environment where it collides with a route.
        """
        user = await _user(session, "slug@example.com")

        with pytest.raises(ApiError) as exc:
            await _service(session, user).create(CardCreate(display_name="X", slug="admin"))
        assert exc.value.code == "CARD_SLUG_RESERVED"

    async def test_taken_slug_is_refused(self, session: AsyncSession) -> None:
        alice = await _user(session, "a-slug@example.com")
        bob = await _user(session, "b-slug@example.com")
        await _service(session, alice).create(CardCreate(display_name="A", slug="sarah"))

        with pytest.raises(ApiError) as exc:
            await _service(session, bob).create(CardCreate(display_name="B", slug="sarah"))
        assert exc.value.code == "CARD_SLUG_TAKEN"

    @pytest.mark.parametrize(
        "slug",
        [
            "ab",
            "-lead",
            "trail-",
            "double--hyphen",
            "has space",
            "x" * 41,
            "under_score",
            "dot.dot",
            "emoji-\U0001f600",
        ],
    )
    async def test_malformed_slugs_are_refused(self, session: AsyncSession, slug: str) -> None:
        user = await _user(session, f"bad-{abs(hash(slug))}@example.com")
        with pytest.raises(ApiError) as exc:
            await _service(session, user).create(CardCreate(display_name="X", slug=slug))
        assert exc.value.code == "CARD_SLUG_INVALID"

    async def test_mixed_case_is_normalised_not_rejected(self, session: AsyncSession) -> None:
        """Someone typing `Sarah` means `sarah`.

        Rejecting it would be pedantry; silently issuing a different slug from
        the one they typed would be worse. Normalise and validate.
        """
        user = await _user(session, "case@example.com")
        with pytest.raises(ApiError) as exc:
            await _service(session, user).create(CardCreate(display_name="X", slug="Admin"))
        assert exc.value.code == "CARD_SLUG_RESERVED"


class TestQrCustomisation:
    async def test_requires_entitlement(self, session: AsyncSession) -> None:
        user = await _user(session, "qr@example.com")
        with pytest.raises(ApiError) as exc:
            await _service(session, user).create(
                CardCreate(display_name="X", qr_style={"foreground": "#000000"})
            )
        assert exc.value.code == "BILLING_ENTITLEMENT_MISSING"

    async def test_low_contrast_is_refused_at_save_time(self, session: AsyncSession) -> None:
        """Not at scan time.

        A pale-on-white code fails in front of another person and arrives as
        "your app is broken" rather than as a colour complaint (ADR-0017).
        """
        user = await _user(session, "qr2@example.com")
        await _grant(session, user, "card.qr_customisation", value=True)

        with pytest.raises(ApiError) as exc:
            await _service(session, user).create(
                CardCreate(
                    display_name="X",
                    qr_style={"foreground": "#ffe600", "background": "#ffffff"},
                )
            )
        assert exc.value.code == "CARD_QR_CONTRAST_INSUFFICIENT"

    async def test_sufficient_contrast_is_accepted(self, session: AsyncSession) -> None:
        user = await _user(session, "qr3@example.com")
        await _grant(session, user, "card.qr_customisation", value=True)
        await _service(session, user).create(
            CardCreate(
                display_name="X",
                qr_style={"foreground": "#1a2a6b", "background": "#ffffff"},
            )
        )

    def test_contrast_ratio_matches_wcag(self) -> None:
        assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
        assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)


class TestTokens:
    async def test_every_card_gets_a_static_token_on_creation(self, session: AsyncSession) -> None:
        """Minting lazily would make the first export the first chance to fail."""
        user = await _user(session, "tok@example.com")
        card = await _service(session, user).create(CardCreate(display_name="X"))

        tokens = list(
            (await session.execute(select(CardToken).where(CardToken.card_id == card.id))).scalars()
        )
        assert [t.kind for t in tokens] == [TokenKind.STATIC]

    async def test_static_token_has_no_expiry_and_is_idempotent(
        self, session: AsyncSession
    ) -> None:
        """Fetching must not invalidate what is already printed."""
        user = await _user(session, "tok2@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X"))

        first = await service.mint_static_token(card.id)
        second = await service.mint_static_token(card.id)

        assert first.token == second.token
        assert first.expires_at is None

    async def test_live_token_expires(self, session: AsyncSession) -> None:
        user = await _user(session, "tok3@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X"))

        issued = await service.mint_live_token(card.id)
        assert issued.kind is TokenKind.LIVE
        assert issued.expires_at is not None
        assert issued.expires_at > datetime.now(UTC)
        assert issued.expires_at < datetime.now(UTC) + timedelta(hours=1)

    async def test_tokens_are_not_uuids(self, session: AsyncSession) -> None:
        """A UUIDv7 token would leak its creation time to anyone holding a
        badge photograph, and 36 characters makes a denser, less scannable
        code (ADR-0010)."""
        from uuid import UUID

        user = await _user(session, "tok4@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X"))
        issued = await service.mint_static_token(card.id)

        assert len(issued.token) == 22
        with pytest.raises(ValueError):
            UUID(issued.token)

    async def test_rotation_kills_the_old_token(self, session: AsyncSession) -> None:
        """The remedy when a badge photograph leaks."""
        user = await _user(session, "rot@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X"))
        old = await service.mint_static_token(card.id)

        new = await service.rotate_static_token(card.id)
        assert new.token != old.token

        with pytest.raises(ApiError) as exc:
            await TokenResolver(session).resolve(old.token)
        assert exc.value.code == "TOKEN_REVOKED"

        resolved, _ = await TokenResolver(session).resolve(new.token)
        assert resolved.id == card.id


class TestTokenResolution:
    async def test_expired_and_revoked_are_distinguishable(self, session: AsyncSession) -> None:
        """Collapsing these into one response would leave a user whose badge
        was rotated with no idea why their code stopped working."""
        user = await _user(session, "res@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X"))

        live = await service.mint_live_token(card.id)
        token_row = (
            await session.execute(select(CardToken).where(CardToken.token == live.token))
        ).scalar_one()
        token_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.flush()

        with pytest.raises(ApiError) as exc:
            await TokenResolver(session).resolve(live.token)
        assert exc.value.code == "TOKEN_EXPIRED"

    async def test_unknown_token_is_not_found(self, session: AsyncSession) -> None:
        with pytest.raises(ApiError) as exc:
            await TokenResolver(session).resolve("nope")
        assert exc.value.code == "TOKEN_NOT_FOUND"

    async def test_deleted_card_is_indistinguishable_from_a_missing_token(
        self, session: AsyncSession
    ) -> None:
        """A suspended card must not be distinguishable from one that never
        existed (runbooks/abuse-takedown.md)."""
        user = await _user(session, "del@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X"))
        issued = await service.mint_static_token(card.id)
        await service.delete(card.id)

        with pytest.raises(ApiError) as exc:
            await TokenResolver(session).resolve(issued.token)
        assert exc.value.code in {"TOKEN_NOT_FOUND", "TOKEN_REVOKED"}


class TestCardIsolation:
    async def test_another_users_card_is_not_found(self, session: AsyncSession) -> None:
        alice = await _user(session, "iso-a@example.com")
        bob = await _user(session, "iso-b@example.com")
        bobs = await _service(session, bob).create(CardCreate(display_name="Bob"))

        with pytest.raises(ApiError) as exc:
            await _service(session, alice).get(bobs.id)
        assert exc.value.code == "CARD_NOT_FOUND"


class TestUpdate:
    async def test_patch_leaves_unsent_fields_alone(self, session: AsyncSession) -> None:
        """Without exclude_unset a PATCH silently blanks every field the client
        did not send."""
        user = await _user(session, "patch@example.com")
        service = _service(session, user)
        card = await service.create(
            CardCreate(display_name="X", headline="Engineer", company="Acme Corp")
        )

        await service.update(card.id, CardUpdate(headline="Founder"))

        assert card.headline == "Founder"
        assert card.company == "Acme Corp"

    async def test_explicit_null_clears_a_field(self, session: AsyncSession) -> None:
        user = await _user(session, "patch2@example.com")
        service = _service(session, user)
        card = await service.create(CardCreate(display_name="X", company="Acme Corp"))

        await service.update(card.id, CardUpdate.model_validate({"company": None}))
        assert card.company is None


class TestEntitlementsResolver:
    async def test_unlimited_beats_a_finite_grant(self, session: AsyncSession) -> None:
        """-1 is unlimited, so it must beat max(). Getting this backwards would
        cap a paying customer at whatever the other row says."""
        from acme.domains.billing.service import EntitlementsService, Subject

        user = await _user(session, "ent@example.com")
        await _grant(session, user, "card.limit", value=5, source=EntitlementSource.STRIPE)
        await _grant(session, user, "card.limit", value=-1, source=EntitlementSource.APPLE)

        resolved = await EntitlementsService(session).limit(Subject.user(user.id), "card.limit")
        assert resolved == -1

    async def test_unknown_key_raises_rather_than_defaulting(self, session: AsyncSession) -> None:
        """A typo must not silently resolve to False and lock someone out."""
        from acme.domains.billing.service import EntitlementsService, Subject

        user = await _user(session, "ent2@example.com")
        with pytest.raises(KeyError):
            await EntitlementsService(session).check(Subject.user(user.id), "card.limitt")
