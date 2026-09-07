"""The connection list: notes, tags, reminders, merge, and the anonymous path.

The retention surface. Notes are the highest-value user-authored data in the
product, so most of these tests are about not losing them.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_id
from acme.core.repository import Tenant
from acme.domains.billing.enums import EntitlementSource, SubjectKind
from acme.domains.billing.models import Entitlement
from acme.domains.cards.schemas import CardCreate
from acme.domains.cards.service import CardsService
from acme.domains.connections.anonymous import AnonymousScanService, truncate_ip
from acme.domains.connections.enums import ScanChannel
from acme.domains.connections.models import AnonymousScan, ConnectionView
from acme.domains.connections.schemas import ConnectionUpdate
from acme.domains.connections.service import ConnectionListService
from acme.domains.events.models import Event, EventAttendee
from acme.domains.exchange.schemas import ExchangeRequest
from acme.domains.exchange.service import ExchangeService
from acme.domains.identity.models import Organization, User, UserProfile

pytestmark = pytest.mark.integration


class Actor:
    def __init__(self, user: User, card_id: object) -> None:
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


async def _connect(
    session: AsyncSession, scanner: Actor, target: Actor, *, event_id: object = None
) -> ConnectionView:
    token = (
        await CardsService(session, Tenant.user(target.id)).mint_live_token(
            target.card_id  # type: ignore[arg-type]
        )
    ).token
    result = await ExchangeService(session, scanner.id).exchange(
        ExchangeRequest(
            token=token,
            card_id=scanner.card_id,  # type: ignore[arg-type]
            event_id=event_id,  # type: ignore[arg-type]
        )
    )
    view = (
        await session.execute(
            select(ConnectionView).where(
                ConnectionView.connection_id == result.connection_id,
                ConnectionView.user_id == scanner.id,
            )
        )
    ).scalar_one()
    return view


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


class TestListing:
    async def test_shows_the_counterpart_not_yourself(self, session: AsyncSession) -> None:
        alice = await _actor(session, "l-alice")
        bob = await _actor(session, "l-bob")
        await _connect(session, alice, bob)

        page = await ConnectionListService(session, alice.id).page()
        assert len(page.items) == 1
        assert page.items[0].counterpart.display_name == "L-Bob"

    async def test_history_is_never_gated(self, session: AsyncSession) -> None:
        """A free user must see every contact they ever made.

        Hiding them reads as theft and would generate more one-star reviews
        than every other issue combined (00-context/pricing.md).
        """
        alice = await _actor(session, "gate-a")
        for i in range(5):
            other = await _actor(session, f"gate-b{i}")
            await _connect(session, alice, other)

        page = await ConnectionListService(session, alice.id).page()
        assert len(page.items) == 5

    async def test_another_users_connections_are_invisible(self, session: AsyncSession) -> None:
        alice = await _actor(session, "vis-a")
        bob = await _actor(session, "vis-b")
        carol = await _actor(session, "vis-c")
        await _connect(session, bob, carol)

        page = await ConnectionListService(session, alice.id).page()
        assert page.items == []

    async def test_cursor_pagination_does_not_repeat_rows(self, session: AsyncSession) -> None:
        """Offset pagination skips and duplicates as rows are inserted, and
        this table only grows."""
        alice = await _actor(session, "cur-a")
        for i in range(5):
            await _connect(session, alice, await _actor(session, f"cur-b{i}"))

        service = ConnectionListService(session, alice.id)
        first = await service.page(limit=2)
        second = await service.page(limit=2, cursor=first.next_cursor)

        assert first.next_cursor is not None
        ids = {i.id for i in first.items} | {i.id for i in second.items}
        assert len(ids) == 4


class TestNotes:
    async def test_note_is_private_to_its_owner(self, session: AsyncSession) -> None:
        """The structural guarantee (ADR-0003).

        Notes contain things like "seemed unprepared, low priority". They live
        in the writer's own row, so the subject cannot reach them through any
        query.
        """
        alice = await _actor(session, "n-alice")
        bob = await _actor(session, "n-bob")
        view = await _connect(session, alice, bob)

        await ConnectionListService(session, alice.id).update(
            view.id, ConnectionUpdate(note="low priority")
        )

        bobs = await ConnectionListService(session, bob.id).page()
        assert bobs.items[0].note is None

    async def test_note_conflict_is_preserved_not_overwritten(self, session: AsyncSession) -> None:
        """Two devices edited offline. Both texts survive with a marker; the
        user resolves it (ADR-0016). Visible complexity beats invisible loss.
        """
        alice = await _actor(session, "nc-a")
        bob = await _actor(session, "nc-b")
        view = await _connect(session, alice, bob)
        view.note = "from phone"
        view.note_conflict = "from tablet"
        await session.flush()

        page = await ConnectionListService(session, alice.id).page()
        assert page.items[0].note_conflict == "from tablet"

        await ConnectionListService(session, alice.id).update(
            view.id, ConnectionUpdate(note="merged", resolve_note_conflict=True)
        )
        assert view.note_conflict is None


class TestTags:
    async def test_tags_are_deduplicated_and_ordered(self, session: AsyncSession) -> None:
        """So two clients writing the same set do not produce a spurious
        difference on the next sync."""
        alice = await _actor(session, "t-a")
        bob = await _actor(session, "t-b")
        view = await _connect(session, alice, bob)

        result = await ConnectionListService(session, alice.id).update(
            view.id, ConnectionUpdate(tags=["lead", "berlin", "lead"])
        )
        assert result.tags == ["berlin", "lead"]

    async def test_filter_by_tag(self, session: AsyncSession) -> None:
        alice = await _actor(session, "tf-a")
        tagged = await _connect(session, alice, await _actor(session, "tf-b"))
        await _connect(session, alice, await _actor(session, "tf-c"))
        service = ConnectionListService(session, alice.id)
        await service.update(tagged.id, ConnectionUpdate(tags=["lead"]))

        page = await service.page(tag="lead")
        assert len(page.items) == 1


class TestReminders:
    async def test_free_tier_caps_active_reminders(self, session: AsyncSession) -> None:
        """Capped, not removed. A free user has to see the feature work before
        they will pay to uncap it."""
        alice = await _actor(session, "r-a")
        service = ConnectionListService(session, alice.id)
        due = datetime.now(UTC) + timedelta(days=1)

        for i in range(3):
            view = await _connect(session, alice, await _actor(session, f"r-b{i}"))
            await service.update(view.id, ConnectionUpdate(reminder_at=due))

        fourth = await _connect(session, alice, await _actor(session, "r-c"))
        with pytest.raises(ApiError) as exc:
            await service.update(fourth.id, ConnectionUpdate(reminder_at=due))
        assert exc.value.code == "CONNECTION_REMINDER_LIMIT_REACHED"

    async def test_entitlement_uncaps_reminders(self, session: AsyncSession) -> None:
        alice = await _actor(session, "r-d")
        session.add(
            Entitlement(
                subject_kind=SubjectKind.USER,
                subject_id=alice.id,
                entitlement_key="connection.reminder_limit",
                value_int=-1,
                source=EntitlementSource.MANUAL,
            )
        )
        await session.flush()

        service = ConnectionListService(session, alice.id)
        due = datetime.now(UTC) + timedelta(days=1)
        for i in range(5):
            view = await _connect(session, alice, await _actor(session, f"r-e{i}"))
            await service.update(view.id, ConnectionUpdate(reminder_at=due))

    async def test_replacing_a_reminder_does_not_count_as_a_new_one(
        self, session: AsyncSession
    ) -> None:
        alice = await _actor(session, "r-f")
        service = ConnectionListService(session, alice.id)
        due = datetime.now(UTC) + timedelta(days=1)

        views = []
        for i in range(3):
            view = await _connect(session, alice, await _actor(session, f"r-g{i}"))
            await service.update(view.id, ConnectionUpdate(reminder_at=due))
            views.append(view)

        await service.update(views[0].id, ConnectionUpdate(reminder_at=due + timedelta(days=1)))


class TestMerge:
    async def test_merging_the_same_person_keeps_both_notes(self, session: AsyncSession) -> None:
        """Notes are concatenated, never discarded. Losing one to a merge is
        unacceptable."""
        alice = await _actor(session, "m-a")
        bob = await _actor(session, "m-b")
        event = await _event(session, alice, alice, bob)

        outside = await _connect(session, alice, bob)
        inside = await _connect(session, alice, bob, event_id=event.id)

        service = ConnectionListService(session, alice.id)
        await service.update(outside.id, ConnectionUpdate(note="met at a bar"))
        await service.update(inside.id, ConnectionUpdate(note="met at DevCon"))

        merged = await service.merge(outside.id, [inside.id])
        assert "met at a bar" in (merged.note or "")
        assert "met at DevCon" in (merged.note or "")

    async def test_merge_unions_tags(self, session: AsyncSession) -> None:
        alice = await _actor(session, "m-c")
        bob = await _actor(session, "m-d")
        event = await _event(session, alice, alice, bob)
        a = await _connect(session, alice, bob)
        b = await _connect(session, alice, bob, event_id=event.id)

        service = ConnectionListService(session, alice.id)
        await service.update(a.id, ConnectionUpdate(tags=["lead"]))
        await service.update(b.id, ConnectionUpdate(tags=["berlin"]))

        merged = await service.merge(a.id, [b.id])
        assert merged.tags == ["berlin", "lead"]

    async def test_merged_view_disappears_from_the_list(self, session: AsyncSession) -> None:
        alice = await _actor(session, "m-e")
        bob = await _actor(session, "m-f")
        event = await _event(session, alice, alice, bob)
        a = await _connect(session, alice, bob)
        b = await _connect(session, alice, bob, event_id=event.id)

        service = ConnectionListService(session, alice.id)
        await service.merge(a.id, [b.id])

        page = await service.page()
        assert len(page.items) == 1

    async def test_merging_different_people_is_refused(self, session: AsyncSession) -> None:
        """Silently loses one of them, and the user would not find out until
        they went looking for someone no longer there."""
        alice = await _actor(session, "m-g")
        bob = await _actor(session, "m-h")
        carol = await _actor(session, "m-i")
        a = await _connect(session, alice, bob)
        c = await _connect(session, alice, carol)

        with pytest.raises(ApiError) as exc:
            await ConnectionListService(session, alice.id).merge(a.id, [c.id])
        assert exc.value.code == "CONNECTION_MERGE_INVALID"

    async def test_duplicates_are_surfaced_not_merged_automatically(
        self, session: AsyncSession
    ) -> None:
        """Two meetings at different events are legitimately two records until
        the user says otherwise."""
        alice = await _actor(session, "m-j")
        bob = await _actor(session, "m-k")
        event = await _event(session, alice, alice, bob)
        await _connect(session, alice, bob)
        await _connect(session, alice, bob, event_id=event.id)

        duplicates = await ConnectionListService(session, alice.id).find_duplicates()
        assert list(duplicates) == [bob.id]
        assert len(duplicates[bob.id]) == 2


class TestDeletion:
    async def test_deletion_is_asymmetric(self, session: AsyncSession) -> None:
        """My removing a contact must not erase your record of the same
        meeting (ADR-0003)."""
        alice = await _actor(session, "d-a")
        bob = await _actor(session, "d-b")
        view = await _connect(session, alice, bob)

        await ConnectionListService(session, alice.id).delete(view.id)

        assert (await ConnectionListService(session, alice.id).page()).items == []
        assert len((await ConnectionListService(session, bob.id).page()).items) == 1


class TestAnonymousScan:
    async def test_scan_is_counted_without_an_account(self, session: AsyncSession) -> None:
        """The majority path, and the growth loop (ADR-0008)."""
        bob = await _actor(session, "anon-b")
        token = (
            await CardsService(session, Tenant.user(bob.id)).mint_static_token(
                bob.card_id  # type: ignore[arg-type]
            )
        ).token

        recorded = await AnonymousScanService(session).record(
            token=token, channel=ScanChannel.QR_STATIC
        )
        assert recorded.card_owner_id == bob.id

        scan = await session.get(AnonymousScan, recorded.scan_id)
        assert scan is not None
        assert scan.saved_vcard is False

    async def test_saved_vcard_is_the_conversion_metric(self, session: AsyncSession) -> None:
        """Views are vanity; a save means the contact landed in a phone."""
        bob = await _actor(session, "anon-c")
        token = (
            await CardsService(session, Tenant.user(bob.id)).mint_static_token(
                bob.card_id  # type: ignore[arg-type]
            )
        ).token
        service = AnonymousScanService(session)
        recorded = await service.record(token=token, channel=ScanChannel.QR_STATIC)

        await service.mark_saved(recorded.scan_id)
        scan = await session.get(AnonymousScan, recorded.scan_id)
        assert scan is not None and scan.saved_vcard is True

    async def test_reply_is_idempotent(self, session: AsyncSession) -> None:
        """A double submit must not leave the card owner two pending requests
        to dismiss."""
        bob = await _actor(session, "anon-d")
        token = (
            await CardsService(session, Tenant.user(bob.id)).mint_static_token(
                bob.card_id  # type: ignore[arg-type]
            )
        ).token
        service = AnonymousScanService(session)
        recorded = await service.record(token=token, channel=ScanChannel.QR_STATIC)

        await service.leave_details(recorded.scan_id, email="stranger@example.com", name="Stranger")
        await service.leave_details(
            recorded.scan_id, email="someone-else@example.com", name="Other"
        )

        scan = await session.get(AnonymousScan, recorded.scan_id)
        assert scan is not None
        assert scan.reply_email == "stranger@example.com"

    async def test_claiming_attaches_pending_scans_to_a_new_account(
        self, session: AsyncSession
    ) -> None:
        """The loop closing: scan a badge, leave an email, install a week
        later, and the connection is waiting rather than lost."""
        bob = await _actor(session, "anon-e")
        token = (
            await CardsService(session, Tenant.user(bob.id)).mint_static_token(
                bob.card_id  # type: ignore[arg-type]
            )
        ).token
        service = AnonymousScanService(session)
        recorded = await service.record(token=token, channel=ScanChannel.QR_STATIC)
        await service.leave_details(recorded.scan_id, email="later@example.com", name="L")

        newcomer = await _actor(session, "anon-f")
        claimed = await service.claim_for(newcomer.id, "later@example.com")

        assert claimed == 1
        scan = await session.get(AnonymousScan, recorded.scan_id)
        assert scan is not None and scan.claimed_by_user_id == newcomer.id

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("203.0.113.42", "203.0.113.0"),
            ("2001:db8:85a3:8d3:1319:8a2e:370:7348", "2001:db8:85a3::"),
            (None, None),
            ("garbage", None),
        ],
    )
    def test_ip_is_truncated_never_stored_whole(
        self, raw: str | None, expected: str | None
    ) -> None:
        """Coarse geography needs a lawful basis and disclosure (ADR-0012).
        Storing the exact address is a much larger commitment for the same
        "viewed in Berlin" feature."""
        assert truncate_ip(raw) == expected
