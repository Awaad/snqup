"""Cards service.

Owns card lifecycle and token minting. The token rules here are the security
model of the whole product (ADR-0002) - read TokenKind before changing them.
"""

import re
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
from acme.domains.cards.schemas import CardCreate, CardUpdate
from acme.domains.identity.service import IdentityService

# Live tokens are short-lived because presenting one IS the consent to a
# symmetric exchange (ADR-0002). Long enough to survive queueing for a coffee,
# short enough that a screenshot is worthless within the hour.
LIVE_TOKEN_TTL = timedelta(minutes=15)

# Lowercase, digits, hyphen. No leading or trailing hyphen, no doubles.
# Deliberately narrow: slugs appear in URLs, in QR payloads, and are read aloud.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MIN, SLUG_MAX = 3, 40

# Minimum contrast ratio between QR foreground and background. Below roughly
# 3:1 scanning degrades badly in the bad lighting where scanning actually
# happens, and it arrives as "your app is broken" rather than as a colour
# complaint (ADR-0017).
MIN_QR_CONTRAST = 3.0


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
        if not SLUG_MIN <= len(slug) <= SLUG_MAX:
            raise ApiError(
                "CARD_SLUG_INVALID",
                status_code=422,
                message=f"slug must be {SLUG_MIN}-{SLUG_MAX} characters",
            )
        if not SLUG_PATTERN.match(slug):
            raise ApiError(
                "CARD_SLUG_INVALID",
                status_code=422,
                message="slug may contain lowercase letters, digits and hyphens",
            )

        if await self._identity.is_slug_reserved(slug):
            # Route collisions, brand squatting and profanity. Checked before
            # the unique index so the caller gets a specific reason rather than
            # a generic conflict.
            raise ApiError("CARD_SLUG_RESERVED", status_code=422, message="slug is reserved")

        existing = await self._cards.by_slug(slug)
        if existing is not None:
            raise ApiError("CARD_SLUG_TAKEN", status_code=409, message="slug in use")

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
            await self._assert_slug_available(payload.slug)
        if payload.qr_style:
            await self._assert_qr_style_allowed(payload.qr_style)
        if payload.custom_fields:
            await self._assert_custom_fields_allowed()

        # First card is the default whether or not the caller said so: a user
        # with cards but no default has no card to present.
        is_default = payload.is_default or await self._cards.count() == 0
        if is_default:
            await self._cards.clear_default()

        # is_default is computed above, not taken from the payload: the first
        # card is the default whether or not the caller asked for it.
        attributes = payload.model_dump()
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

    async def list(self) -> list[Card]:
        return await self._cards.list(limit=100)

    async def update(self, card_id: UUID, payload: CardUpdate) -> Card:
        card = await self.get(card_id)
        # exclude_unset, so an absent field is left alone and an explicit null
        # clears it. Without this a PATCH would silently blank every field the
        # client did not send.
        changes = payload.model_dump(exclude_unset=True)

        if "slug" in changes and changes["slug"] is not None and changes["slug"] != card.slug:
            await self._assert_slug_available(str(changes["slug"]))

        if changes.get("qr_style"):
            await self._assert_qr_style_allowed(changes["qr_style"])
        if changes.get("custom_fields"):
            await self._assert_custom_fields_allowed()

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


class TokenResolver:
    """Resolves a public token. Used by the scan endpoint and the public page.

    Deliberately NOT part of CardsService: it runs for a stranger with no
    account and no tenant, so it must not depend on anything tenant-scoped.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tokens = CardTokenRepository(session)

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
