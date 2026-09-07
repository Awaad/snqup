"""The public scan surface.

Unauthenticated by design (api/public_routes.py). This is the highest-traffic
surface in the product and the one that works for the majority who have no
account.
"""

from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from acme.api.deps import RateLimiterDep, SessionDep
from acme.domains.cards.schemas import PublicCardOut
from acme.domains.cards.service import TokenResolver
from acme.domains.connections.anonymous import AnonymousScanService
from acme.domains.connections.enums import InteractionKind, ScanChannel

router = APIRouter(prefix="/v1", tags=["public"])

# Per static token. A card resolving hundreds of times an hour is scraping, not
# a conference. Throttle rather than block: a false positive at a live event,
# in front of four hundred people, is worse than an unthrottled hour (ADR-0007).
SCAN_LIMIT_PER_TOKEN = 200
SCAN_WINDOW_SECONDS = 3600

# The reply form is the tightest limit here: an unauthenticated write from a
# stranger, so it is the obvious spam target.
REPLY_LIMIT_PER_IP = 5
REPLY_WINDOW_SECONDS = 3600


class ScanResult(BaseModel):
    scan_id: UUID
    card: PublicCardOut


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str | None = Field(default=None, max_length=120)
    #: Anything else they chose to share. Free-form because we have no idea yet
    #: what people put here, and finding out is the point.
    payload: dict[str, str] = Field(default_factory=dict)
    #: Honeypot. A real browser leaves it empty; a bot fills every field.
    website: str = ""


def _client_ip(request: Request) -> str | None:
    # Cloudflare sits in front of everything, so the connecting address is a
    # proxy. Trust the forwarded header only because nothing reaches the origin
    # without passing through it.
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/scan/{token}", response_model=ScanResult)
async def record_scan(
    token: str,
    request: Request,
    session: SessionDep,
    limiter: RateLimiterDep,
    channel: ScanChannel = ScanChannel.QR_STATIC,
    event_id: UUID | None = None,
) -> ScanResult:
    """Resolve a scanned token and count the scan.

    No authentication: most scanners have no account, and that is the growth
    loop rather than an edge case (ADR-0008).

    Rate limiting FAILS OPEN. If the limiter is unavailable the scan proceeds,
    because a live event is exactly when the limiter is most loaded and least
    worth trusting.
    """
    await limiter.check(
        f"scan:token:{token}",
        limit=SCAN_LIMIT_PER_TOKEN,
        window_seconds=SCAN_WINDOW_SECONDS,
    )

    target = await TokenResolver(session).resolve_public(token)
    recorded = await AnonymousScanService(session).record(
        token=token,
        channel=channel,
        event_id=event_id,
        ip=_client_ip(request),
    )
    return ScanResult(scan_id=recorded.scan_id, card=target.public)


class InteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: InteractionKind
    #: Which element was used ('phone', 'linkedin'). NEVER the content - a copy
    #: event carries no copied text.
    target: str | None = Field(default=None, max_length=60)


@router.post("/scan/{scan_id}/interactions", status_code=status.HTTP_204_NO_CONTENT)
async def record_interaction(
    scan_id: UUID, payload: InteractionRequest, session: SessionDep
) -> None:
    """What the scanner did with the card.

    Views are vanity, but "conversion" is broader than a vCard save: tapping
    the phone number, opening a LinkedIn link or copying an email are all real
    engagement. The client fires this on tel:/mailto:/link taps, on the browser
    `copy` event, and on save and Wallet-add.

    Idempotent per (scan, kind, target) - clients retry, and a double-tap must
    not double-count a conversion.

    Screenshots are undetectable, so every figure built on this is a LOWER
    BOUND. Say "at least" wherever it surfaces.
    """
    await AnonymousScanService(session).record_interaction(
        scan_id, payload.kind, target=payload.target
    )


@router.post("/scan/{scan_id}/reply", status_code=status.HTTP_204_NO_CONTENT)
async def leave_details(
    scan_id: UUID,
    payload: ReplyRequest,
    request: Request,
    session: SessionDep,
    limiter: RateLimiterDep,
) -> None:
    """Optionally leave your own details back.

    OPTIONAL is the point: requiring this before the vCard download would kill
    the save flow, which is the one thing this page must get right.

    Creates a pending request the card owner sees immediately, and an
    invitation to claim once the sender installs.
    """
    if payload.website:
        # Honeypot filled: a bot. Return success rather than an error, so it
        # learns nothing about which field gave it away.
        return

    ip = _client_ip(request)
    await limiter.check(
        f"scan:reply:{ip}",
        limit=REPLY_LIMIT_PER_IP,
        window_seconds=REPLY_WINDOW_SECONDS,
    )

    await AnonymousScanService(session).leave_details(
        scan_id,
        email=str(payload.email),
        name=payload.name,
        payload=dict(payload.payload),
    )
