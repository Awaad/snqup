"""Background jobs and the privacy surface.

The purge job matters most and is the easiest to not notice failing: soft
delete without it is a claim made to a regulator and not kept (ADR-0020).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.ids import new_id
from acme.core.repository import Tenant
from acme.domains.cards.schemas import CardCreate
from acme.domains.cards.service import CardsService
from acme.domains.connections.models import ConnectionView
from acme.domains.connections.schemas import ConnectionUpdate
from acme.domains.connections.service import ConnectionListService
from acme.domains.events.models import Event, EventAttendee
from acme.domains.exchange.schemas import ExchangeRequest
from acme.domains.exchange.service import ExchangeService
from acme.domains.identity.models import Organization, User, UserProfile
from acme.domains.identity.service import IdentityService
from acme.domains.notifications.models import Notification
from acme.domains.safety.export import DataExportService
from acme.workers import jobs

pytestmark = pytest.mark.integration


async def _actor(session: AsyncSession, name: str):
    user = User()
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, auth_subject=f"s-{name}", email=f"{name}@example.com"))
    await session.flush()
    card = await CardsService(session, Tenant.user(user.id)).create(
        CardCreate(display_name=name.title(), company="Acme Corp")
    )
    return user, card.id


async def _connect(session: AsyncSession, a, b, *, event_id=None):
    a_user, a_card = a
    b_user, b_card = b
    token = (await CardsService(session, Tenant.user(b_user.id)).mint_live_token(b_card)).token
    result = await ExchangeService(session, a_user.id).exchange(
        ExchangeRequest(token=token, card_id=a_card, event_id=event_id)
    )
    return (
        await session.execute(
            select(ConnectionView).where(
                ConnectionView.connection_id == result.connection_id,
                ConnectionView.user_id == a_user.id,
            )
        )
    ).scalar_one()


class TestReminders:
    async def test_due_reminders_produce_notifications(self, session: AsyncSession) -> None:
        """A push is best-effort, so the durable record is the row."""
        alice = await _actor(session, "j-alice")
        bob = await _actor(session, "j-bob")
        view = await _connect(session, alice, bob)
        await ConnectionListService(session, alice[0].id).update(
            view.id,
            ConnectionUpdate(reminder_at=datetime.now(UTC) - timedelta(minutes=1)),
        )

        assert await jobs.send_due_reminders(session) == 1

        rows = list(
            (
                await session.execute(
                    select(Notification).where(Notification.kind == "reminder_due")
                )
            ).scalars()
        )
        assert len(rows) == 1

    async def test_a_reminder_is_never_sent_twice(self, session: AsyncSession) -> None:
        """Marked done in the SAME transaction as the notification. Marking
        first loses it on a crash; marking after re-sends on a retry."""
        alice = await _actor(session, "j-c")
        bob = await _actor(session, "j-d")
        view = await _connect(session, alice, bob)
        await ConnectionListService(session, alice[0].id).update(
            view.id,
            ConnectionUpdate(reminder_at=datetime.now(UTC) - timedelta(minutes=1)),
        )

        assert await jobs.send_due_reminders(session) == 1
        assert await jobs.send_due_reminders(session) == 0

    async def test_future_reminders_are_left_alone(self, session: AsyncSession) -> None:
        alice = await _actor(session, "j-e")
        bob = await _actor(session, "j-f")
        view = await _connect(session, alice, bob)
        await ConnectionListService(session, alice[0].id).update(
            view.id,
            ConnectionUpdate(reminder_at=datetime.now(UTC) + timedelta(days=1)),
        )
        assert await jobs.send_due_reminders(session) == 0


class TestPostEventDigest:
    async def _event(self, session: AsyncSession, host, *attendees, ended_hours_ago: int):
        org = Organization(name="Org", slug=f"p-{new_id().hex[:10]}", is_personal=True)
        session.add(org)
        await session.flush()
        ends = datetime.now(UTC) - timedelta(hours=ended_hours_ago)
        event = Event(
            organization_id=org.id,
            created_by=host[0].id,
            name="DevCon",
            code=f"C{new_id().hex[:8]}",
            starts_at=ends - timedelta(hours=8),
            ends_at=ends,
            timezone="UTC",
        )
        session.add(event)
        await session.flush()
        for a in attendees:
            session.add(EventAttendee(event_id=event.id, user_id=a[0].id))
        await session.flush()
        return event

    async def test_digest_fires_a_day_after_the_event(self, session: AsyncSession) -> None:
        """The single best retention mechanic: it reactivates the app while the
        connections are still warm."""
        alice = await _actor(session, "d-a")
        bob = await _actor(session, "d-b")
        event = await self._event(session, alice, alice, bob, ended_hours_ago=25)
        await _connect(session, alice, bob, event_id=event.id)

        assert await jobs.send_post_event_digests(session) >= 1
        rows = list(
            (
                await session.execute(
                    select(Notification).where(Notification.kind == "post_event_digest")
                )
            ).scalars()
        )
        assert rows
        assert rows[0].payload["connections"] == 1

    async def test_digest_does_not_fire_before_24_hours(self, session: AsyncSession) -> None:
        alice = await _actor(session, "d-c")
        bob = await _actor(session, "d-d")
        event = await self._event(session, alice, alice, bob, ended_hours_ago=2)
        await _connect(session, alice, bob, event_id=event.id)

        assert await jobs.send_post_event_digests(session) == 0

    async def test_digest_is_sent_once_per_attendee(self, session: AsyncSession) -> None:
        """The scheduler may run a window twice, and a duplicate digest is the
        kind of thing people unsubscribe over."""
        alice = await _actor(session, "d-e")
        bob = await _actor(session, "d-f")
        event = await self._event(session, alice, alice, bob, ended_hours_ago=25)
        await _connect(session, alice, bob, event_id=event.id)

        first = await jobs.send_post_event_digests(session)
        second = await jobs.send_post_event_digests(session)
        assert first >= 1
        assert second == 0

    async def test_no_digest_for_someone_who_met_nobody(self, session: AsyncSession) -> None:
        """ "You met 0 people" is a worse message than silence."""
        alice = await _actor(session, "d-g")
        lonely = await _actor(session, "d-h")
        await self._event(session, alice, alice, lonely, ended_hours_ago=25)

        assert await jobs.send_post_event_digests(session) == 0


class TestPurge:
    async def test_purge_erases_the_profile_and_keeps_the_anchor(
        self, session: AsyncSession
    ) -> None:
        """Erasure is a single DELETE of user_profiles.

        The anchor survives so the counterparty keeps their record of a meeting
        that happened - connections reference it with ON DELETE RESTRICT.
        """
        alice = await _actor(session, "p-a")
        bob = await _actor(session, "p-b")
        view = await _connect(session, alice, bob)
        await ConnectionListService(session, bob[0].id).update(
            (
                await session.execute(
                    select(ConnectionView).where(
                        ConnectionView.connection_id == view.connection_id,
                        ConnectionView.user_id == bob[0].id,
                    )
                )
            )
            .scalar_one()
            .id,
            ConnectionUpdate(note="met at the coffee stand"),
        )

        await IdentityService(session).schedule_deletion(
            alice[0].id, datetime.now(UTC) - timedelta(seconds=1)
        )

        assert await jobs.purge_deleted(session) == 1

        assert await session.get(UserProfile, alice[0].id) is None
        assert await session.get(User, alice[0].id) is not None

        bobs = await ConnectionListService(session, bob[0].id).page()
        assert len(bobs.items) == 1
        assert bobs.items[0].note == "met at the coffee stand"

    async def test_purge_leaves_accounts_inside_the_grace_period(
        self, session: AsyncSession
    ) -> None:
        """The grace period turns accidental deletion from a catastrophe into a
        support conversation."""
        alice = await _actor(session, "p-c")
        await IdentityService(session).schedule_deletion(
            alice[0].id, datetime.now(UTC) + timedelta(days=30)
        )

        assert await jobs.purge_deleted(session) == 0
        assert await session.get(UserProfile, alice[0].id) is not None

    async def test_purge_removes_the_erased_users_own_views(self, session: AsyncSession) -> None:
        alice = await _actor(session, "p-d")
        bob = await _actor(session, "p-e")
        await _connect(session, alice, bob)

        await IdentityService(session).schedule_deletion(
            alice[0].id, datetime.now(UTC) - timedelta(seconds=1)
        )
        await jobs.purge_deleted(session)

        remaining = list(
            (
                await session.execute(
                    select(ConnectionView).where(ConnectionView.user_id == alice[0].id)
                )
            ).scalars()
        )
        assert remaining == []


class TestGdprExport:
    async def test_export_contains_the_callers_own_data(self, session: AsyncSession) -> None:
        alice = await _actor(session, "x-a")
        bob = await _actor(session, "x-b")
        view = await _connect(session, alice, bob)
        await ConnectionListService(session, alice[0].id).update(
            view.id, ConnectionUpdate(note="my private note", tags=["lead"])
        )

        payload = await DataExportService(session).build(alice[0].id)

        assert payload["profile"]["email"] == "x-a@example.com"
        assert len(payload["cards"]) == 1
        assert payload["connections"][0]["my_note"] == "my private note"
        assert payload["connections"][0]["my_tags"] == ["lead"]

    async def test_export_excludes_the_counterparts_private_notes(
        self, session: AsyncSession
    ) -> None:
        """The leak an export is most likely to cause.

        The counterpart's note about this meeting is THEIR data, not the
        requester's, and returning it would undo exactly what the edge/view
        split protects (ADR-0003).
        """
        alice = await _actor(session, "x-c")
        bob = await _actor(session, "x-d")
        view = await _connect(session, alice, bob)
        bobs_view = (
            await session.execute(
                select(ConnectionView).where(
                    ConnectionView.connection_id == view.connection_id,
                    ConnectionView.user_id == bob[0].id,
                )
            )
        ).scalar_one()
        await ConnectionListService(session, bob[0].id).update(
            bobs_view.id, ConnectionUpdate(note="bob thinks alice is pushy")
        )

        payload = await DataExportService(session).build(alice[0].id)
        assert "pushy" not in str(payload)

    async def test_export_is_machine_readable(self, session: AsyncSession) -> None:
        """Article 20 says structured and machine-readable, not a PDF."""
        import json

        alice = await _actor(session, "x-e")
        raw = await DataExportService(session).build_json(alice[0].id)
        assert json.loads(raw)["format_version"] == 1
