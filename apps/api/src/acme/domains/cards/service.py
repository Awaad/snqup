"""Cards service.

Owns card lifecycle and token minting. The token rules here are the security
model of the whole product (ADR-0002) - read TokenKind before changing them.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_token
from acme.core.repository import Tenant
from acme.domains.billing.service import EntitlementsService, Subject
from acme.domains.cards.enums import TokenKind
from acme.domains.cards.models import Card, CardToken
from acme.domains.cards.repository import CardRepository, CardTokenRepository
from acme.domains.cards.schemas import CardCreate, CardUpdate, PublicCardOut
from acme.domains.identity.service import IdentityService

# Live tokens are short-lived because presenting one IS the consent to a
# symmetric exchange (ADR-0002). Long enough to survive queueing for a coffee,
# short enough that a screenshot is worthless within the hour.
LIVE_TOKEN_TTL = timedelta(minutes=15)

# Minimum contrast ratio between QR foreground and background. Below roughly
# 3:1 scanning degrades badly in the bad lighting where scanning actually
# happens, and it arrives as "your app is broken" rather than as a colour
# complaint (ADR-0017).
MIN_QR_CONTRAST = 3.0


@dataclass(frozen=True, slots=True)
class ResolvedCard:
    """What another domain gets when it resolves a card.

    A value object, not the ORM model. Returning `Card` would leak an ORM
    object across a domain boundary - which import-linter rejects - and would
    let the caller lazy-load its way into anything reachable from it.

    Carries exactly what the exchange needs: who owns it, which card it is, the
    immutable snapshot, and the public projection to hand back.
    """

    card_id: UUID
    owner_id: UUID
    snapshot: dict[str, object]
    public: PublicCardOut
    token_kind: TokenKind | None = None


def build_snapshot(card: Card) -> dict[str, object]:
    """The immutable record of what was actually exchanged (ADR-0004).

    Without it, someone could present as "Engineer at Acme", exchange with 200
    people, then rewrite the card to "Recruiter at Competitor" and
    retroactively change what everyone received.

    Deliberately the PUBLIC projection: a snapshot is what the other person
    saw, not our internal row.
    """
    return {
        "display_name": card.display_name,
        "headline": card.headline,
        "company": card.company,
        "email": card.email,
        "phone": card.phone,
        "website": card.website,
        "photo_path": card.photo_path,
        "socials": dict(card.socials),
        "custom_fields": list(card.custom_fields),
        "theme": dict(card.theme),
        "theme_version": card.theme_version,
    }


def resolved(card: Card, token_kind: TokenKind | None = None) -> ResolvedCard:
    return ResolvedCard(
        card_id=card.id,
        owner_id=card.user_id,
        snapshot=build_snapshot(card),
        public=PublicCardOut.model_validate(card),
        token_kind=token_kind,
    )


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    kind: TokenKind
    expires_at: datetime | None


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ApiError("VALIDATION_FAILED", status_code=422, message=f"bad colour {hex_colour!r}")

    def channel(component: int) -> float:
        srgb = component / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(value[i : i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    a, b = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


class CardsService:
    def __init__(self, session: AsyncSession, tenant: Tenant) -> None:
        self._session = session
        self._tenant = tenant
        self._cards = CardRepository(session, tenant)
        self._tokens = CardTokenRepository(session)
        self._entitlements = EntitlementsService(session)
        self._identity = IdentityService(session)

    def _subject(self) -> Subject:
        return (
            Subject.user(self._tenant.id)
            if self._tenant.kind == "user"
            else Subject.organization(self._tenant.id)
        )

    async def _assert_slug_available(self, slug: str) -> None:
        """Format, reserved list, then uniqueness.

        Format and the reserved list live in identity, because cards,
        organizations and events all end up as a URL a stranger reads - `admin`
        must be refused whichever surface asks. Three copies of that check is
        how one of them ends up out of date.
        """
        await self._identity.assert_slug_available(slug, kind="card")

        if await self._cards.by_slug(slug) is not None:
            raise ApiError("CARD_SLUG_TAKEN", status_code=409, message="slug in use")

    async def _assert_links_allowed(self, count: int) -> None:
        """Cap custom links, never socials.

        Socials are identity; capping them would make a free card look broken.
        Custom links are where the link-in-bio value sits, so this is the
        honest paywall (00-context/pricing.md).
        """
        if count == 0:
            return
        limit = await self._entitlements.limit(self._subject(), "link.custom_limit")
        if limit != -1 and count > limit:
            raise ApiError(
                "CARD_LINK_LIMIT_REACHED",
                status_code=403,
                message=f"plan allows {limit} custom link(s)",
                details={"limit": limit},
            )

    async def _assert_custom_fields_allowed(self) -> None:
        if not await self._entitlements.allowed(self._subject(), "card.custom_fields"):
            raise ApiError(
                "CARD_CUSTOM_FIELDS_NOT_ENTITLED",
                status_code=403,
                message="custom fields require a paid plan",
            )

    async def _assert_qr_style_allowed(self, qr_style: dict[str, str]) -> None:
        if not qr_style:
            return
        if not await self._entitlements.allowed(self._subject(), "card.qr_customisation"):
            raise ApiError(
                "BILLING_ENTITLEMENT_MISSING",
                status_code=403,
                message="QR customisation requires a paid plan",
                details={"entitlement": "card.qr_customisation"},
            )

        foreground = qr_style.get("foreground")
        background = qr_style.get("background")
        if foreground and background:
            ratio = contrast_ratio(foreground, background)
            if ratio < MIN_QR_CONTRAST:
                # Refused at save time, not at scan time. A pale-on-white code
                # fails in front of another person and is reported as a broken
                # app.
                raise ApiError(
                    "CARD_QR_CONTRAST_INSUFFICIENT",
                    status_code=422,
                    message=f"contrast {ratio:.1f}:1 is below {MIN_QR_CONTRAST}:1",
                    details={"ratio": round(ratio, 2), "minimum": MIN_QR_CONTRAST},
                )

    async def create(self, payload: CardCreate) -> Card:
        limit = await self._entitlements.limit(self._subject(), "card.limit")
        if limit != -1 and await self._cards.count() >= limit:
            raise ApiError(
                "CARD_LIMIT_REACHED",
                status_code=403,
                message=f"plan allows {limit} card(s)",
                details={"limit": limit},
            )

        if payload.slug is not None:
            # Normalised before validation AND before storage, so the slug that
            # was checked is the slug that gets saved. Someone typing "Sarah"
            # means "sarah"; storing something different from what they typed
            # would be worse than rejecting it.
            normalised_slug = payload.slug.strip().lower()
            payload = payload.model_copy(update={"slug": normalised_slug})
            await self._assert_slug_available(normalised_slug)
        if payload.qr_style:
            await self._assert_qr_style_allowed(payload.qr_style)
        if payload.custom_fields:
            await self._assert_custom_fields_allowed()
        await self._assert_links_allowed(len(payload.links))

        # First card is the default whether or not the caller said so: a user
        # with cards but no default has no card to present.
        is_default = payload.is_default or await self._cards.count() == 0
        if is_default:
            await self._cards.clear_default()

        # is_default is computed above, not taken from the payload: the first
        # card is the default whether or not the caller asked for it.
        # mode="json" because HttpUrl and enums are not JSON-serialisable and
        # `links` is a JSONB column. Without it every card with a link fails on
        # INSERT with "Object of type HttpUrl is not JSON serializable" - at
        # the database, not at validation, so the error points at SQL rather
        # than at the field.
        attributes = payload.model_dump(mode="json")
        attributes["is_default"] = is_default
        card = self._cards.add(Card(**attributes))
        await self._session.flush()

        # Every card gets a static token immediately. Without one the card
        # cannot be printed, exported, added to a Wallet or written to NFC, and
        # minting lazily means the first export is the first chance to fail.
        await self.mint_static_token(card.id)
        return card

    async def get(self, card_id: UUID) -> Card:
        card = await self._cards.get(card_id)
        if card is None:
            raise ApiError("CARD_NOT_FOUND", status_code=404)
        return card

    async def get_resolved(self, card_id: UUID) -> ResolvedCard:
        """The caller's own card, as a value object."""
        return resolved(await self.get(card_id))

    async def list_cards(self) -> list[Card]:
        """Named `list_cards`, not `list`.

        A method called `list` shadows the builtin inside the class body, so
        any method defined AFTER it that annotates `list[...]` fails to
        typecheck with "Function ... is not valid as a type" - an error message
        pointing nowhere near the cause. Latent rather than theoretical: this
        class had it until a probe proved it.
        """
        return await self._cards.list(limit=100)

    async def update(self, card_id: UUID, payload: CardUpdate) -> Card:
        card = await self.get(card_id)
        # exclude_unset, so an absent field is left alone and an explicit null
        # clears it. Without this a PATCH would silently blank every field the
        # client did not send.
        changes = payload.model_dump(exclude_unset=True, mode="json")

        if "slug" in changes and changes["slug"] is not None and changes["slug"] != card.slug:
            await self._assert_slug_available(str(changes["slug"]))

        if changes.get("qr_style"):
            await self._assert_qr_style_allowed(changes["qr_style"])
        if changes.get("custom_fields"):
            await self._assert_custom_fields_allowed()
        if changes.get("links") is not None:
            await self._assert_links_allowed(len(changes["links"]))

        if changes.pop("is_default", False):
            await self._cards.clear_default()
            card.is_default = True

        for key, value in changes.items():
            setattr(card, key, value)

        await self._session.flush()
        return card

    async def delete(self, card_id: UUID) -> None:
        """Soft delete, and revoke the static token.

        Existing connections keep their card snapshots (ADR-0004): a deleted
        card must not erase someone else's record of the meeting. Only the live
        view degrades to "no longer available".
        """
        card = await self.get(card_id)
        card.deleted_at = datetime.now(UTC)
        await self._tokens.revoke_static(card.id)
        await self._session.flush()

    # -- tokens (ADR-0002) ------------------------------------------------

    async def mint_live_token(self, card_id: UUID) -> IssuedToken:
        """Short-lived token that produces a SYMMETRIC exchange.

        Rendered in-app only, never exported. The owner had to open the app and
        present it, and that act is the consent - which is the entire reason
        symmetric exchange is safe.
        """
        card = await self.get(card_id)
        expires_at = datetime.now(UTC) + LIVE_TOKEN_TTL
        token = self._tokens.add(
            CardToken(
                card_id=card.id,
                kind=TokenKind.LIVE,
                token=new_token(),
                expires_at=expires_at,
            )
        )
        await self._session.flush()
        return IssuedToken(token.token, TokenKind.LIVE, expires_at)

    async def mint_static_token(self, card_id: UUID) -> IssuedToken:
        """Long-lived ONE-WAY token for badges, exports, NFC and Wallet.

        Never produces an automatic exchange. A static token can be
        photographed off a badge without the owner knowing, so the scanner
        receives the card and the owner receives a pending request. That
        asymmetry is the harvesting vector this design exists to close.

        Idempotent: a card has at most one live static token, so calling this
        twice returns the same one rather than quietly invalidating whatever is
        already printed. Use rotate_static_token() to replace it.
        """
        card = await self.get(card_id)
        existing = await self._tokens.active_static(card.id)
        if existing is not None:
            return IssuedToken(existing.token, TokenKind.STATIC, None)

        token = self._tokens.add(
            CardToken(card_id=card.id, kind=TokenKind.STATIC, token=new_token())
        )
        await self._session.flush()
        return IssuedToken(token.token, TokenKind.STATIC, None)

    async def rotate_static_token(self, card_id: UUID) -> IssuedToken:
        """Kill the current static token and issue a new one.

        The remedy when a badge photograph leaks. Everything already printed
        stops resolving, which is the point - and why this is a deliberate
        action rather than something that happens on every card edit.
        """
        card = await self.get(card_id)
        await self._tokens.revoke_static(card.id)
        token = self._tokens.add(
            CardToken(card_id=card.id, kind=TokenKind.STATIC, token=new_token())
        )
        await self._session.flush()
        return IssuedToken(token.token, TokenKind.STATIC, None)


class PublicCardService:
    """Slug lookup for the link-in-bio page.

    NOT tenant-scoped and deliberately not part of CardsService: it runs for a
    stranger with no account and no tenant, so it must not depend on anything
    that assumes one.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_slug(self, slug: str) -> ResolvedCard | None:
        stmt = select(Card).where(Card.slug == slug.strip().lower(), Card.deleted_at.is_(None))
        card = (await self._session.execute(stmt)).scalar_one_or_none()
        return resolved(card) if card is not None else None


class TokenResolver:
    """Resolves a public token. Used by the scan endpoint and the public page.

    Deliberately NOT part of CardsService: it runs for a stranger with no
    account and no tenant, so it must not depend on anything tenant-scoped.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tokens = CardTokenRepository(session)

    async def resolve_public(self, token: str) -> ResolvedCard:
        """Resolve to a value object. The cross-domain entry point."""
        card, found = await self.resolve(token)
        return resolved(card, found.kind)

    async def resolve(self, token: str) -> tuple[Card, CardToken]:
        found = await self._tokens.resolve(token)
        if found is None:
            raise ApiError("TOKEN_NOT_FOUND", status_code=404)

        # Distinct codes on purpose: a user whose badge was rotated needs to
        # know the code was killed, not that it never existed.
        if found.revoked_at is not None:
            raise ApiError("TOKEN_REVOKED", status_code=410)
        if found.expires_at is not None and found.expires_at <= datetime.now(UTC):
            raise ApiError("TOKEN_EXPIRED", status_code=410)

        stmt = select(Card).where(Card.id == found.card_id, Card.deleted_at.is_(None))
        card = (await self._session.execute(stmt)).scalar_one_or_none()
        if card is None:
            # The card was deleted or suspended. Same code as a missing token:
            # a suspended card must not be distinguishable from one that never
            # existed (runbooks/abuse-takedown.md).
            raise ApiError("TOKEN_NOT_FOUND", status_code=404)

        return card, found
