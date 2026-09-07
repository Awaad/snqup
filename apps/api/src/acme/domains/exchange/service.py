"""The exchange.

The most important code in the product. Every path needs test coverage and the
rules below are load-bearing, not stylistic.

Orchestrates four domains in ONE transaction and owns no tables of its own
(ADR-0025). The commit happens in the request dependency, so a failure anywhere
leaves nothing behind.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.repository import Tenant
from acme.domains.cards.enums import TokenKind
from acme.domains.cards.service import CardsService, TokenResolver
from acme.domains.connections.enums import ConnectionState, ScanChannel
from acme.domains.connections.service import ConnectionsService
from acme.domains.events.service import EventsService
from acme.domains.exchange.schemas import ExchangeRequest, ExchangeResult
from acme.domains.safety.service import SafetyService

# How far outside an event window an occurred_at may fall before we stop
# trusting it. Device clocks drift, and a client can set one deliberately, so
# a claimed time is validated rather than accepted (ADR-0027).
OCCURRED_AT_TOLERANCE = timedelta(hours=6)

# Channels a LIVE token may legitimately arrive on. A live token is rendered
# in-app only, so it cannot have come from a printed badge or an NFC tag - a
# request claiming otherwise is either a bug or someone probing.
LIVE_CHANNELS = {ScanChannel.QR_LIVE}


class ExchangeService:
    def __init__(self, session: AsyncSession, scanner_id: UUID) -> None:
        self._session = session
        self._scanner_id = scanner_id
        self._tokens = TokenResolver(session)
        self._cards = CardsService(session, Tenant.user(scanner_id))
        self._connections = ConnectionsService(session)
        self._safety = SafetyService(session)
        self._events = EventsService(session)

    def _resolve_occurred_at(
        self, claimed: datetime | None, *, event_window: tuple[datetime, datetime] | None
    ) -> datetime:
        now = datetime.now(UTC)
        if claimed is None:
            return now

        if claimed.tzinfo is None:
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message="occurred_at must be timezone-aware",
            )

        # A future timestamp is always wrong and would put the exchange after
        # the event ends on every activity chart.
        if claimed > now + timedelta(minutes=5):
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message="occurred_at is in the future",
            )

        if event_window is not None:
            starts_at, ends_at = event_window
            if not (
                starts_at - OCCURRED_AT_TOLERANCE <= claimed <= ends_at + OCCURRED_AT_TOLERANCE
            ):
                # Outside the event by more than tolerance means a wrong clock.
                # Fall back to server time rather than rejecting: losing the
                # exchange is far worse than losing its precise minute.
                return now

        return claimed

    async def exchange(self, request: ExchangeRequest) -> ExchangeResult:
        target = await self._tokens.resolve_public(request.token)

        # The scanner's own card. Goes through CardsService, so scanning with
        # someone else's card id is CARD_NOT_FOUND rather than an impersonation.
        scanner = await self._cards.get_resolved(request.card_id)

        if target.owner_id == self._scanner_id:
            raise ApiError("SCAN_SELF", status_code=422, message="cannot scan your own card")

        # Checked BEFORE anything is written. A block that only prevents the
        # notification still leaves the connection in the database.
        if await self._safety.is_blocked(self._scanner_id, target.owner_id):
            # Same code in both directions, and no indication of who blocked
            # whom: blocks are private (runbooks/abuse-takedown.md).
            raise ApiError("SCAN_BLOCKED", status_code=403)

        # THE RULE THIS FILE EXISTS FOR (ADR-0002).
        #
        # A live token means the owner opened the app and presented it, and
        # that act is the consent - so the exchange is symmetric.
        #
        # A static token can be photographed off a badge without the owner ever
        # knowing. The scanner receives the card; the owner receives a PENDING
        # request. Never make this symmetric.
        symmetric = target.token_kind == TokenKind.LIVE

        if symmetric and request.channel not in LIVE_CHANNELS:
            # A live token cannot have arrived from a badge, an NFC tag or a
            # link - those carry static tokens. Trust the token, not the claim.
            raise ApiError(
                "VALIDATION_FAILED",
                status_code=422,
                message=f"live token cannot arrive via {request.channel.value}",
            )

        event_window = None
        if request.event_id is not None:
            event_window = await self._events.window_for_attendee(
                request.event_id, self._scanner_id
            )

        occurred_at = self._resolve_occurred_at(request.occurred_at, event_window=event_window)

        existing = await self._connections.find_existing(
            self._scanner_id, target.owner_id, request.event_id
        )
        if existing is not None:
            # Idempotent by construction. An offline queue retries, a user taps
            # twice, two devices sync the same exchange - all must be one
            # connection and an identical response (ADR-0016).
            return ExchangeResult(
                connection_id=existing.id,
                state=existing.state,
                symmetric=symmetric,
                card=target.public,
                occurred_at=existing.occurred_at,
                duplicate=True,
            )

        connection = await self._connections.record(
            user_a=self._scanner_id,
            card_a_id=scanner.card_id,
            snapshot_a=scanner.snapshot,
            user_b=target.owner_id,
            card_b_id=target.card_id,
            snapshot_b=target.snapshot,
            channel=request.channel,
            occurred_at=occurred_at,
            event_id=request.event_id,
            state=ConnectionState.CONFIRMED if symmetric else ConnectionState.PENDING,
        )

        if request.note:
            # The scanner's own view only. The other party never sees it -
            # notes live in connection_views precisely so a query bug cannot
            # leak one (ADR-0003).
            view = await self._connections.view_for(connection.id, self._scanner_id)
            if view is not None:
                view.note = request.note
                view.note_updated_at = occurred_at

        await self._session.flush()

        return ExchangeResult(
            connection_id=connection.id,
            state=connection.state,
            symmetric=symmetric,
            card=target.public,
            occurred_at=connection.occurred_at,
        )
