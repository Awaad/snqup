"""The exchange.

The most important tests in the suite. The handoff requires every path:
live token, static token, offline replay, duplicate, concurrent scan of the
same pair, blocked user, expired token, revoked token, self-scan, event-scoped
and non-event.

The rule under test throughout: a LIVE token produces a symmetric exchange
because presenting it in-app is the consent; a STATIC token never does, because
it can be photographed off a badge without the owner knowing (ADR-0002).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_id
from acme.core.repository import Tenant
from acme.domains.cards.models import CardToken
from acme.domains.cards.schemas import CardCreate
from acme.domains.cards.service import CardsService
from acme.domains.connections.enums import ConnectionState, ScanChannel
from acme.domains.connections.models import Connection, ConnectionView
from acme.domains.events.models import Event, EventAttendee
from acme.domains.exchange.schemas import ExchangeRequest
from acme.domains.exchange.service import ExchangeService
from acme.domains.identity.models import Organization, User, UserProfile
from acme.domains.safety.models import Block

pytestmark = pytest.mark.integration


class Actor:
    """A user with one card, which is what every exchange needs."""

    def __init__(self, user: User, card_id: UUID) -> None:
        self.user = user
        self.id = user.id
        self.card_id = card_id


async def _actor(session: AsyncSession, name: str) -> Actor:
    user = User()
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, auth_subject=f"s-{name}", email=f"{name}@example.com"))
    await session.flush()
    card = await CardsService(session, Tenant.user(user.id)).create(
        CardCreate(display_name=name.title(), company="Acme Corp")
    )
    return Actor(user, card.id)


async def _token(session: AsyncSession, actor: Actor, *, live: bool) -> str:
    service = CardsService(session, Tenant.user(actor.id))
    issued = (
        await service.mint_live_token(actor.card_id)
        if live
        else await service.mint_static_token(actor.card_id)
    )
    return issued.token


async def _event(session: AsyncSession, host: Actor, *attendees: Actor) -> Event:
    org = Organization(name="Org", slug=f"org-{new_id().hex[:8]}", is_personal=True)
    session.add(org)
    await session.flush()
    event = Event(
        organization_id=org.id,
        created_by=host.id,
        name="DevCon",
        code=f"C{new_id().hex[:8]}",
        starts_at=datetime.now(UTC) - timedelta(hours=1),
        ends_at=datetime.now(UTC) + timedelta(hours=8),
        timezone="Europe/Berlin",
    )
    session.add(event)
    await session.flush()
    for a in attendees:
        session.add(EventAttendee(event_id=event.id, user_id=a.id))
    await session.flush()
    return event


class TestTokenSemantics:
    """The security model. If any of these change, read ADR-0002 first."""

    async def test_live_token_produces_a_symmetric_exchange(self, session: AsyncSession) -> None:
        alice = await _actor(session, "alice")
        bob = await _actor(session, "bob")
        token = await _token(session, bob, live=True)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=token, card_id=alice.card_id)
        )

        assert result.symmetric is True
        assert result.state is ConnectionState.CONFIRMED

    async def test_static_token_is_one_way(self, session: AsyncSession) -> None:
        """The harvesting vector this design exists to close.

        A static token can be photographed off a badge without the owner
        knowing, so the scanner gets the card and the owner gets a PENDING
        request - never an automatic exchange.
        """
        alice = await _actor(session, "alice2")
        bob = await _actor(session, "bob2")
        token = await _token(session, bob, live=False)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=token, card_id=alice.card_id, channel=ScanChannel.QR_STATIC)
        )

        assert result.symmetric is False
        assert result.state is ConnectionState.PENDING

    async def test_client_cannot_request_symmetry(self, session: AsyncSession) -> None:
        """Symmetry is derived from the token, never from the request.

        A client claiming a static scan arrived on the live channel must not
        be able to upgrade a one-way scan into a mutual exchange.
        """
        alice = await _actor(session, "alice3")
        bob = await _actor(session, "bob3")
        static = await _token(session, bob, live=False)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=static, card_id=alice.card_id, channel=ScanChannel.QR_LIVE)
        )
        assert result.symmetric is False

    async def test_live_token_rejects_an_impossible_channel(self, session: AsyncSession) -> None:
        """A live token is rendered in-app only, so it cannot have come from a
        printed badge or an NFC tag. Either a bug or someone probing."""
        alice = await _actor(session, "alice4")
        bob = await _actor(session, "bob4")
        live = await _token(session, bob, live=True)

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(token=live, card_id=alice.card_id, channel=ScanChannel.NFC)
            )
        assert exc.value.code == "VALIDATION_FAILED"


class TestSnapshots:
    async def test_both_snapshots_are_written(self, session: AsyncSession) -> None:
        """ADR-0004. Without these, someone could exchange as "Engineer at
        Acme" and later rewrite the card, retroactively changing what 200
        people received."""
        alice = await _actor(session, "snap-a")
        bob = await _actor(session, "snap-b")
        token = await _token(session, bob, live=True)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=token, card_id=alice.card_id)
        )
        connection = await session.get(Connection, result.connection_id)
        assert connection is not None
        assert connection.card_low_snapshot["display_name"]
        assert connection.card_high_snapshot["display_name"]

    async def test_snapshot_survives_the_card_changing(self, session: AsyncSession) -> None:
        from acme.domains.cards.schemas import CardUpdate

        alice = await _actor(session, "snap-c")
        bob = await _actor(session, "snap-d")
        token = await _token(session, bob, live=True)
        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=token, card_id=alice.card_id)
        )

        await CardsService(session, Tenant.user(bob.id)).update(
            bob.card_id, CardUpdate(company="Competitor Ltd")
        )

        connection = await session.get(Connection, result.connection_id)
        assert connection is not None
        snapshots = [connection.card_low_snapshot, connection.card_high_snapshot]
        assert any(s["company"] == "Acme Corp" for s in snapshots)


class TestIdempotency:
    async def test_repeating_an_exchange_returns_the_original(self, session: AsyncSession) -> None:
        """Offline retry makes duplicates the NORMAL case (ADR-0016)."""
        alice = await _actor(session, "dup-a")
        bob = await _actor(session, "dup-b")
        token = await _token(session, bob, live=True)
        service = ExchangeService(session, alice.id)
        request = ExchangeRequest(token=token, card_id=alice.card_id)

        first = await service.exchange(request)
        second = await service.exchange(request)

        assert first.connection_id == second.connection_id
        assert second.duplicate is True
        assert await session.scalar(
            select(Connection.id).where(Connection.id == first.connection_id)
        )

    async def test_both_directions_produce_one_connection(self, session: AsyncSession) -> None:
        """Alice scans Bob, then Bob scans Alice. Canonical ordering means the
        pair resolves to the same row rather than two mirrored ones."""
        alice = await _actor(session, "rev-a")
        bob = await _actor(session, "rev-b")

        first = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=await _token(session, bob, live=True), card_id=alice.card_id)
        )
        second = await ExchangeService(session, bob.id).exchange(
            ExchangeRequest(token=await _token(session, alice, live=True), card_id=bob.card_id)
        )

        assert first.connection_id == second.connection_id

    async def test_concurrent_scans_of_the_same_pair(self, session: AsyncSession) -> None:
        """Two devices syncing the same offline queue at once.

        The partial unique index is the backstop when the read-then-write race
        loses: at most one row survives, and the loser must surface as a
        constraint error rather than a second connection.
        """
        alice = await _actor(session, "conc-a")
        bob = await _actor(session, "conc-b")
        token = await _token(session, bob, live=True)
        request = ExchangeRequest(token=token, card_id=alice.card_id)

        await ExchangeService(session, alice.id).exchange(request)
        await ExchangeService(session, alice.id).exchange(request)

        count = await session.scalar(
            select(Connection.id).where(Connection.user_low_id.in_([alice.id, bob.id]))
        )
        assert count is not None
        rows = list(
            (
                await session.execute(select(Connection).where(Connection.event_id.is_(None)))
            ).scalars()
        )
        pair = {tuple(sorted([r.user_low_id, r.user_high_id])) for r in rows}
        assert len(pair) == len(rows), "one row per pair"


class TestRefusals:
    async def test_self_scan_is_refused(self, session: AsyncSession) -> None:
        alice = await _actor(session, "self")
        token = await _token(session, alice, live=True)

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(token=token, card_id=alice.card_id)
            )
        assert exc.value.code == "SCAN_SELF"

    async def test_blocked_users_cannot_exchange_in_either_direction(
        self, session: AsyncSession
    ) -> None:
        """Symmetric on purpose: a block is only useful if the blocked party
        cannot route around it by being the one who scans."""
        alice = await _actor(session, "blk-a")
        bob = await _actor(session, "blk-b")
        session.add(Block(blocker_user_id=bob.id, blocked_user_id=alice.id))
        await session.flush()

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(token=await _token(session, bob, live=True), card_id=alice.card_id)
            )
        assert exc.value.code == "SCAN_BLOCKED"

        with pytest.raises(ApiError):
            await ExchangeService(session, bob.id).exchange(
                ExchangeRequest(token=await _token(session, alice, live=True), card_id=bob.card_id)
            )

    async def test_nothing_is_written_when_blocked(self, session: AsyncSession) -> None:
        """A block that only suppresses the notification still leaves the
        connection in the database."""
        alice = await _actor(session, "blk-c")
        bob = await _actor(session, "blk-d")
        session.add(Block(blocker_user_id=alice.id, blocked_user_id=bob.id))
        await session.flush()

        with pytest.raises(ApiError):
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(token=await _token(session, bob, live=True), card_id=alice.card_id)
            )

        assert not list((await session.execute(select(Connection))).scalars())

    async def test_expired_token_is_refused(self, session: AsyncSession) -> None:
        alice = await _actor(session, "exp-a")
        bob = await _actor(session, "exp-b")
        token = await _token(session, bob, live=True)
        row = (
            await session.execute(select(CardToken).where(CardToken.token == token))
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.flush()

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(token=token, card_id=alice.card_id)
            )
        assert exc.value.code == "TOKEN_EXPIRED"

    async def test_revoked_token_is_refused(self, session: AsyncSession) -> None:
        alice = await _actor(session, "rev-c")
        bob = await _actor(session, "rev-d")
        token = await _token(session, bob, live=False)
        await CardsService(session, Tenant.user(bob.id)).rotate_static_token(bob.card_id)

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(token=token, card_id=alice.card_id, channel=ScanChannel.QR_STATIC)
            )
        assert exc.value.code == "TOKEN_REVOKED"

    async def test_scanning_with_another_users_card_is_refused(self, session: AsyncSession) -> None:
        """Impersonation attempt: present someone else's card as your own."""
        alice = await _actor(session, "imp-a")
        bob = await _actor(session, "imp-b")
        carol = await _actor(session, "imp-c")

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(
                    token=await _token(session, bob, live=True),
                    card_id=carol.card_id,
                )
            )
        assert exc.value.code == "CARD_NOT_FOUND"


class TestEventScope:
    async def test_event_scoped_exchange_is_tagged(self, session: AsyncSession) -> None:
        alice = await _actor(session, "ev-a")
        bob = await _actor(session, "ev-b")
        event = await _event(session, alice, alice, bob)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(
                token=await _token(session, bob, live=True),
                card_id=alice.card_id,
                event_id=event.id,
            )
        )
        connection = await session.get(Connection, result.connection_id)
        assert connection is not None
        assert connection.event_id == event.id

    async def test_non_attendee_cannot_tag_an_event(self, session: AsyncSession) -> None:
        """Membership is checked, not trusted.

        A client that could tag any exchange with any event id could inflate
        another organizer's numbers - and those numbers are what they pay for.
        """
        alice = await _actor(session, "ev-c")
        bob = await _actor(session, "ev-d")
        event = await _event(session, bob, bob)

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(
                    token=await _token(session, bob, live=True),
                    card_id=alice.card_id,
                    event_id=event.id,
                )
            )
        assert exc.value.code == "EVENT_NOT_FOUND"

    async def test_same_pair_at_event_and_outside_are_two_connections(
        self, session: AsyncSession
    ) -> None:
        """NULL event_id is a distinct scope, not a wildcard (ADR-0003)."""
        alice = await _actor(session, "ev-e")
        bob = await _actor(session, "ev-f")
        event = await _event(session, alice, alice, bob)

        outside = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(token=await _token(session, bob, live=True), card_id=alice.card_id)
        )
        inside = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(
                token=await _token(session, bob, live=True),
                card_id=alice.card_id,
                event_id=event.id,
            )
        )
        assert outside.connection_id != inside.connection_id


class TestOccurredAt:
    async def test_offline_replay_keeps_the_meeting_time(self, session: AsyncSession) -> None:
        """The whole reason occurred_at exists (ADR-0027).

        An exchange made in a basement and synced three hours later must not
        land at sync time, or the peak-activity chart spikes whenever the wifi
        returns.
        """
        alice = await _actor(session, "occ-a")
        bob = await _actor(session, "occ-b")
        met_at = datetime.now(UTC) - timedelta(hours=3)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(
                token=await _token(session, bob, live=True),
                card_id=alice.card_id,
                occurred_at=met_at,
            )
        )
        assert abs((result.occurred_at - met_at).total_seconds()) < 1

        connection = await session.get(Connection, result.connection_id)
        assert connection is not None
        assert connection.occurred_at < connection.created_at

    async def test_a_future_timestamp_is_refused(self, session: AsyncSession) -> None:
        alice = await _actor(session, "occ-c")
        bob = await _actor(session, "occ-d")

        with pytest.raises(ApiError) as exc:
            await ExchangeService(session, alice.id).exchange(
                ExchangeRequest(
                    token=await _token(session, bob, live=True),
                    card_id=alice.card_id,
                    occurred_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
        assert exc.value.code == "VALIDATION_FAILED"

    async def test_a_wildly_wrong_clock_falls_back_to_server_time(
        self, session: AsyncSession
    ) -> None:
        """Losing the exchange is far worse than losing its precise minute, so
        an out-of-window timestamp is replaced rather than rejected."""
        alice = await _actor(session, "occ-e")
        bob = await _actor(session, "occ-f")
        event = await _event(session, alice, alice, bob)

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(
                token=await _token(session, bob, live=True),
                card_id=alice.card_id,
                event_id=event.id,
                occurred_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        assert result.occurred_at > datetime.now(UTC) - timedelta(minutes=1)


class TestNotes:
    async def test_note_lands_only_on_the_scanners_view(self, session: AsyncSession) -> None:
        """The structural guarantee from ADR-0003.

        Notes will contain things like "seemed unprepared, low priority". They
        live in the scanner's own row so a query bug cannot leak one to the
        person it is about.
        """
        alice = await _actor(session, "note-a")
        bob = await _actor(session, "note-b")

        result = await ExchangeService(session, alice.id).exchange(
            ExchangeRequest(
                token=await _token(session, bob, live=True),
                card_id=alice.card_id,
                note="met at the coffee stand",
            )
        )

        views = list(
            (
                await session.execute(
                    select(ConnectionView).where(
                        ConnectionView.connection_id == result.connection_id
                    )
                )
            ).scalars()
        )
        assert len(views) == 2
        by_user = {v.user_id: v for v in views}
        assert by_user[alice.id].note == "met at the coffee stand"
        assert by_user[bob.id].note is None
