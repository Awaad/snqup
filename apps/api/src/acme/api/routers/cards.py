"""Cards endpoints.

Authenticated card management, plus the two PUBLIC surfaces that make the
product work for someone with no account (api/public_routes.py).

Routers live in the API layer, not inside the domain. ADR-0025 originally
placed a router.py in each domain; import-linter rejected that, correctly - a
router depends on FastAPI dependencies (session, tenant, current user), which
are api-layer concerns, and `acme.domains -> acme.api` is a layer violation.

The domain owns service, repository, models, schemas and enums. The HTTP
surface that wires them to a request lives here.
"""

import contextlib
from uuid import UUID

from fastapi import APIRouter, Request, status

from acme.api.deps import SessionDep, UserTenantDep
from acme.core.errors import ApiError
from acme.core.idempotency import ResponseCache
from acme.domains.cards.schemas import (
    CardCreate,
    CardOut,
    CardUpdate,
    PublicCardOut,
    TokenOut,
)
from acme.domains.cards.service import (
    CardsService,
    PublicCardService,
    TokenResolver,
)

router = APIRouter(prefix="/v1", tags=["cards"])


@router.post("/cards", response_model=CardOut, status_code=status.HTTP_201_CREATED)
async def create_card(payload: CardCreate, session: SessionDep, tenant: UserTenantDep) -> CardOut:
    card = await CardsService(session, tenant).create(payload)
    return CardOut.model_validate(card)


@router.get("/cards", response_model=list[CardOut])
async def list_cards(session: SessionDep, tenant: UserTenantDep) -> list[CardOut]:
    cards = await CardsService(session, tenant).list_cards()
    return [CardOut.model_validate(c) for c in cards]


@router.get("/cards/{card_id}", response_model=CardOut)
async def get_card(card_id: UUID, session: SessionDep, tenant: UserTenantDep) -> CardOut:
    card = await CardsService(session, tenant).get(card_id)
    return CardOut.model_validate(card)


@router.patch("/cards/{card_id}", response_model=CardOut)
async def update_card(
    card_id: UUID, payload: CardUpdate, session: SessionDep, tenant: UserTenantDep
) -> CardOut:
    card = await CardsService(session, tenant).update(card_id, payload)
    return CardOut.model_validate(card)


@router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(card_id: UUID, session: SessionDep, tenant: UserTenantDep) -> None:
    """Soft delete. Connections keep their card snapshots (ADR-0004)."""
    await CardsService(session, tenant).delete(card_id)


# -- tokens (ADR-0002) ----------------------------------------------------


@router.post("/cards/{card_id}/tokens/live", response_model=TokenOut)
async def mint_live_token(card_id: UUID, session: SessionDep, tenant: UserTenantDep) -> TokenOut:
    """Short-lived token producing a SYMMETRIC exchange.

    The mobile app calls this when the QR screen opens and refreshes on
    foreground. Never export the result: presenting it in-app is what makes
    symmetric exchange consensual.
    """
    issued = await CardsService(session, tenant).mint_live_token(card_id)
    return TokenOut(token=issued.token, kind=issued.kind, expires_at=issued.expires_at)


@router.get("/cards/{card_id}/tokens/static", response_model=TokenOut)
async def get_static_token(card_id: UUID, session: SessionDep, tenant: UserTenantDep) -> TokenOut:
    """The card's ONE-WAY token, for badges, exports, NFC and Wallet.

    Idempotent: returns the existing token rather than issuing a new one, so
    fetching it does not invalidate what is already printed.
    """
    issued = await CardsService(session, tenant).mint_static_token(card_id)
    return TokenOut(token=issued.token, kind=issued.kind, expires_at=issued.expires_at)


@router.post("/cards/{card_id}/tokens/static/rotate", response_model=TokenOut)
async def rotate_static_token(
    card_id: UUID, session: SessionDep, tenant: UserTenantDep
) -> TokenOut:
    """Kill the current static token and issue a new one.

    The remedy when a badge photograph leaks. Everything already printed stops
    resolving, which is the point.
    """
    issued = await CardsService(session, tenant).rotate_static_token(card_id)
    return TokenOut(token=issued.token, kind=issued.kind, expires_at=issued.expires_at)


# -- public (api/public_routes.py) ----------------------------------------

public_router = APIRouter(prefix="/v1", tags=["public"])


@public_router.get("/scan/{token}", response_model=PublicCardOut)
async def resolve_scan(token: str, session: SessionDep) -> PublicCardOut:
    """Resolve a scanned token. NO AUTHENTICATION, deliberately.

    Most scanners have no account, and that is the growth loop rather than an
    edge case (ADR-0008). This is the highest-traffic endpoint in the product.

    Returns the public projection only - never the internal card id, which
    would leak a creation timestamp.
    """
    card, _token = await TokenResolver(session).resolve(token)
    return PublicCardOut.model_validate(card)


@public_router.get("/cards/public/{slug}", response_model=PublicCardOut)
async def public_card(slug: str, request: Request, session: SessionDep) -> PublicCardOut:
    """The link-in-bio page. Public, guessable and indexable by design.

    Distinct from /scan/{token}, which is non-guessable and noindex. Same card,
    two access paths with different exposure (ADR-0008).
    """
    # Short-TTL cache. This is the highest-traffic endpoint in the product and
    # carries a sub-one-second budget on hotel wifi. The TTL is deliberately
    # short so a card edit appears quickly - this absorbs the burst when a
    # badge is scanned repeatedly at a stand, rather than acting as a real
    # cache. Cloudflare does the heavy lifting in front.
    cache = ResponseCache(request.app.state.redis)
    cache_key = f"card:slug:{slug.strip().lower()}"

    cached = None
    with contextlib.suppress(Exception):
        # FAILS OPEN: a cache outage must not take down the public page.
        cached = await cache.get(cache_key)
    if cached is not None:
        return PublicCardOut.model_validate(cached)

    resolved = await PublicCardService(session).by_slug(slug)
    if resolved is None:
        raise ApiError("CARD_NOT_FOUND", status_code=404)

    with contextlib.suppress(Exception):
        await cache.set(cache_key, resolved.public.model_dump(mode="json"))
    return resolved.public
