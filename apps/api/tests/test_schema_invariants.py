"""Schema invariants.

Every test here corresponds to a defect found in the pre-migration review
(schema/review-2026-09-05.md). Each one was a real, reproducible failure before
it was fixed.

They live in CI rather than only in that review document because several guard
invariants a future migration could quietly reintroduce. A `CASCADE` that looks
entirely ordinary is what caused the worst of them.

Read the docstrings before changing any constraint these depend on.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

pytestmark = pytest.mark.integration


async def _user(conn: AsyncConnection, uid, email: str) -> str:
    """Create an identity anchor plus its profile."""
    user_id = uid()
    await conn.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": user_id})
    await conn.execute(
        text(
            "INSERT INTO user_profiles (user_id, auth_subject, email, display_name)"
            " VALUES (:id, :sub, :email, :name)"
        ),
        {"id": user_id, "sub": f"sub-{email}", "email": email, "name": email.split("@")[0]},
    )
    return user_id


async def _connection(
    conn: AsyncConnection, uid, low: str, high: str, event_id: str | None = None
) -> str:
    conn_id = uid()
    await conn.execute(
        text(
            "INSERT INTO connections"
            " (id, user_low_id, user_high_id, card_low_snapshot, card_high_snapshot,"
            "  channel, event_id)"
            " VALUES (:id, :low, :high, :snap_low, :snap_high, 'qr_live', :event)"
        ),
        {
            "id": conn_id,
            "low": low,
            "high": high,
            "snap_low": '{"name": "Low"}',
            "snap_high": '{"name": "High"}',
            "event": event_id,
        },
    )
    return conn_id


# ---------------------------------------------------------------------------
# Finding 1 (CRITICAL)
# ---------------------------------------------------------------------------


class TestErasureDoesNotDestroyTheCounterparty:
    """ADR-0020: a card snapshot is like a paper card that was handed over.

    The recipient keeps it. Before the fix, `users -> connections` was
    ON DELETE CASCADE, so erasing account A destroyed the connection row and
    with it B's private note about a meeting B attended.

    B did nothing. B loses their own data. This is the defect that most needs a
    permanent test, because the cause reads as a completely ordinary CASCADE.
    """

    async def test_deleting_the_identity_anchor_is_refused(
        self, conn: AsyncConnection, uid
    ) -> None:
        alice = await _user(conn, uid, "alice@example.com")
        bob = await _user(conn, uid, "bob@example.com")
        low, high = sorted([alice, bob])
        await _connection(conn, uid, low, high)

        # RESTRICT, so this fails loudly instead of silently taking B's record.
        with pytest.raises((IntegrityError, DBAPIError)):
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": alice})

    async def test_erasure_removes_all_pii_and_keeps_the_counterparty_record(
        self, conn: AsyncConnection, uid
    ) -> None:
        alice = await _user(conn, uid, "alice@example.com")
        bob = await _user(conn, uid, "bob@example.com")
        low, high = sorted([alice, bob])
        conn_id = await _connection(conn, uid, low, high)

        await conn.execute(
            text(
                "INSERT INTO connection_views (id, connection_id, user_id, note)"
                " VALUES (:id, :c, :u, :note)"
            ),
            {"id": uid(), "c": conn_id, "u": bob, "note": "met at the coffee stand"},
        )

        # Erasure: one statement.
        await conn.execute(text("DELETE FROM user_profiles WHERE user_id = :id"), {"id": alice})

        pii = await conn.scalar(
            text("SELECT count(*) FROM user_profiles WHERE user_id = :id"),
            {"id": alice},
        )
        assert pii == 0, "Alice's personal data must be gone"

        assert await conn.scalar(text("SELECT count(*) FROM connections")) == 1
        note = await conn.scalar(
            text("SELECT note FROM connection_views WHERE user_id = :id"), {"id": bob}
        )
        assert note == "met at the coffee stand", "B's private note must survive"

        snapshot = await conn.scalar(text("SELECT card_low_snapshot->>'name' FROM connections"))
        assert snapshot is not None, "the snapshot is B's record of who they met"

    async def test_the_anchor_holds_no_personal_data(self, conn: AsyncConnection) -> None:
        """The structural guarantee, not a behaviour.

        If a personal column ever lands on `users`, erasure silently stops
        being complete and no other test would notice. This is why the split
        exists: new personal columns are covered by construction.
        """
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'users'"
                    )
                )
            )
            .scalars()
            .all()
        )

        assert set(columns) == {"id", "created_at", "deleted_at", "purge_after"}, (
            "users is an identity anchor. Personal data belongs in user_profiles, "
            "or erasure stops being a single DELETE. See ADR-0020."
        )


# ---------------------------------------------------------------------------
# Finding 2
# ---------------------------------------------------------------------------


class TestSoftDeleteDoesNotBlockRejoining:
    """Natural PK + deleted_at meant leaving anything was permanent.

    An attendee who left an event by accident could never rejoin. It would have
    presented as a support ticket, not a crash.
    """

    async def test_attendee_can_leave_and_rejoin(self, conn: AsyncConnection, uid) -> None:
        organizer = await _user(conn, uid, "org@example.com")
        event_id = uid()
        await conn.execute(
            text(
                "INSERT INTO events (id, created_by, name, code, starts_at, ends_at,"
                " timezone) VALUES (:id, :by, 'E', 'CODE1', now(),"
                " now() + interval '1 day', 'Europe/Berlin')"
            ),
            {"id": event_id, "by": organizer},
        )

        await conn.execute(
            text("INSERT INTO event_attendees (id, event_id, user_id) VALUES (:id, :e, :u)"),
            {"id": uid(), "e": event_id, "u": organizer},
        )
        await conn.execute(text("UPDATE event_attendees SET deleted_at = now()"))

        await conn.execute(
            text("INSERT INTO event_attendees (id, event_id, user_id) VALUES (:id, :e, :u)"),
            {"id": uid(), "e": event_id, "u": organizer},
        )

        active = await conn.scalar(
            text("SELECT count(*) FROM event_attendees WHERE deleted_at IS NULL")
        )
        assert active == 1

    async def test_double_join_is_still_rejected(self, conn: AsyncConnection, uid) -> None:
        """Allowing rejoin must not have allowed joining twice."""
        organizer = await _user(conn, uid, "org2@example.com")
        event_id = uid()
        await conn.execute(
            text(
                "INSERT INTO events (id, created_by, name, code, starts_at, ends_at,"
                " timezone) VALUES (:id, :by, 'E', 'CODE2', now(),"
                " now() + interval '1 day', 'Europe/Berlin')"
            ),
            {"id": event_id, "by": organizer},
        )
        for _ in range(1):
            await conn.execute(
                text("INSERT INTO event_attendees (id, event_id, user_id) VALUES (:id, :e, :u)"),
                {"id": uid(), "e": event_id, "u": organizer},
            )

        with pytest.raises((IntegrityError, DBAPIError)):
            await conn.execute(
                text("INSERT INTO event_attendees (id, event_id, user_id) VALUES (:id, :e, :u)"),
                {"id": uid(), "e": event_id, "u": organizer},
            )


# ---------------------------------------------------------------------------
# Finding 3
# ---------------------------------------------------------------------------


async def test_soft_deleted_event_releases_its_join_code(conn: AsyncConnection, uid) -> None:
    """A deleted event must not hold its join code hostage forever."""
    organizer = await _user(conn, uid, "org3@example.com")
    for _ in range(2):
        event_id = uid()
        await conn.execute(
            text(
                "INSERT INTO events (id, created_by, name, code, starts_at, ends_at,"
                " timezone) VALUES (:id, :by, 'E', 'REUSED', now(),"
                " now() + interval '1 day', 'Europe/Berlin')"
            ),
            {"id": event_id, "by": organizer},
        )
        await conn.execute(
            text("UPDATE events SET deleted_at = now() WHERE id = :id"),
            {"id": event_id},
        )


async def test_organization_slug_is_unique(conn: AsyncConnection, uid) -> None:
    """Finding 10.

    The fix for finding 3 replaced `slug citext NOT NULL UNIQUE` with a plain
    column, and the partial index meant to replace it never landed. For a while
    two organizations could claim the same public slug and nothing complained.

    Found by the model/database drift test, not by review. A schema edit that
    silently does half of what was intended is exactly what that test is for.
    """
    await conn.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'A', 'taken')"),
        {"id": uid()},
    )

    with pytest.raises((IntegrityError, DBAPIError)):
        await conn.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'B', 'taken')"),
            {"id": uid()},
        )


async def test_soft_deleted_organization_releases_its_slug(conn: AsyncConnection, uid) -> None:
    """Partial, so a deleted organization does not hold its slug forever."""
    first = uid()
    await conn.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'A', 'reused')"),
        {"id": first},
    )
    await conn.execute(
        text("UPDATE organizations SET deleted_at = now() WHERE id = :id"), {"id": first}
    )
    await conn.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'B', 'reused')"),
        {"id": uid()},
    )


# ---------------------------------------------------------------------------
# Finding 5
# ---------------------------------------------------------------------------


async def test_moderation_history_survives_the_moderation_action(
    conn: AsyncConnection, uid
) -> None:
    """Suspending a phishing card then purging it must not erase why.

    Repeat-offender detection and any appeal both depend on that history.
    """
    owner = await _user(conn, uid, "owner@example.com")
    card_id = uid()
    await conn.execute(
        text("INSERT INTO cards (id, user_id, display_name) VALUES (:id, :u, 'Card')"),
        {"id": card_id, "u": owner},
    )
    await conn.execute(
        text(
            "INSERT INTO reports (id, subject_card_id, subject_label, reason, status,"
            " resolution_note) VALUES (:id, :c, 'card: Card', 'phishing', 'actioned',"
            " 'suspended')"
        ),
        {"id": uid(), "c": card_id},
    )

    await conn.execute(text("DELETE FROM cards WHERE id = :id"), {"id": card_id})

    row = (
        await conn.execute(text("SELECT subject_label, reason, resolution_note FROM reports"))
    ).one()
    assert row.reason == "phishing"
    assert row.subject_label == "card: Card", "denormalised so it stays readable"


# ---------------------------------------------------------------------------
# Finding 6
# ---------------------------------------------------------------------------


async def test_entitlement_requires_exactly_one_value(conn: AsyncConnection, uid) -> None:
    """A row with neither value reads as "nothing" or "unlimited".

    Depending on the caller. That is a billing bug in either direction.
    """
    user_id = await _user(conn, uid, "ent@example.com")

    with pytest.raises((IntegrityError, DBAPIError)):
        await conn.execute(
            text(
                "INSERT INTO entitlements (id, subject_kind, subject_id,"
                " entitlement_key, source) VALUES (:id, 'user', :s, 'card.limit',"
                " 'manual')"
            ),
            {"id": uid(), "s": user_id},
        )


# ---------------------------------------------------------------------------
# Finding 7
# ---------------------------------------------------------------------------


async def test_updated_at_actually_moves(conn: AsyncConnection, uid) -> None:
    """It was set at INSERT and never moved, making it decorative.

    Sync conflict resolution (ADR-0016), public page cache invalidation and the
    "details changed since you met" indicator (ADR-0004) all depend on it. All
    three would have failed silently.
    """
    owner = await _user(conn, uid, "upd@example.com")
    card_id = uid()
    await conn.execute(
        text("INSERT INTO cards (id, user_id, display_name) VALUES (:id, :u, 'A')"),
        {"id": card_id, "u": owner},
    )
    await conn.execute(text("SELECT pg_sleep(0.02)"))
    await conn.execute(text("UPDATE cards SET display_name = 'B' WHERE id = :id"), {"id": card_id})

    moved = await conn.scalar(
        text("SELECT updated_at > created_at FROM cards WHERE id = :id"),
        {"id": card_id},
    )
    assert moved is True


async def test_every_updated_at_column_has_a_trigger(conn: AsyncConnection) -> None:
    """Adding the column without the trigger reintroduces finding 7."""
    with_column = set(
        (
            await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE column_name = 'updated_at' AND table_schema = 'public'"
                )
            )
        ).scalars()
    )
    with_trigger = set(
        (
            await conn.execute(
                text(
                    "SELECT event_object_table FROM information_schema.triggers"
                    " WHERE trigger_name LIKE '%updated_at%'"
                )
            )
        ).scalars()
    )

    missing = with_column - with_trigger
    assert not missing, (
        f"tables have updated_at but no trigger: {sorted(missing)}. "
        "Without it the column is decorative. See finding 7."
    )


# ---------------------------------------------------------------------------
# Finding 8
# ---------------------------------------------------------------------------


async def test_duplicate_roster_import_is_rejected(conn: AsyncConnection, uid) -> None:
    """Organizers re-upload registration lists.

    A silent duplicate doubles the expected-attendance denominator, which
    corrupts the one metric organizers actually buy: percentage of attendees
    who made a connection (ADR-0012).
    """
    organizer = await _user(conn, uid, "roster@example.com")
    event_id = uid()
    await conn.execute(
        text(
            "INSERT INTO events (id, created_by, name, code, starts_at, ends_at,"
            " timezone) VALUES (:id, :by, 'E', 'ROSTER1', now(),"
            " now() + interval '1 day', 'Europe/Berlin')"
        ),
        {"id": event_id, "by": organizer},
    )
    await conn.execute(
        text(
            "INSERT INTO event_roster_entries (id, event_id, email)"
            " VALUES (:id, :e, 'dup@example.com')"
        ),
        {"id": uid(), "e": event_id},
    )

    with pytest.raises((IntegrityError, DBAPIError)):
        await conn.execute(
            text(
                "INSERT INTO event_roster_entries (id, event_id, email)"
                " VALUES (:id, :e, 'dup@example.com')"
            ),
            {"id": uid(), "e": event_id},
        )


# ---------------------------------------------------------------------------
# Verified working in review, NOT changed. Guarded so they stay that way.
# ---------------------------------------------------------------------------


class TestConnectionPairInvariants:
    """These were already correct. They are tested because a future refactor
    will try to "simplify" the two partial indexes into one constraint, which
    silently reopens the duplicate-connection hole (ADR-0003).
    """

    async def test_reversed_pair_is_rejected(self, conn: AsyncConnection, uid) -> None:
        a = await _user(conn, uid, "p1@example.com")
        b = await _user(conn, uid, "p2@example.com")
        low, high = sorted([a, b])

        with pytest.raises((IntegrityError, DBAPIError)):
            # high first: violates the canonical ordering CHECK, without which
            # the same pair could be stored twice in opposite order.
            await _connection(conn, uid, high, low)

    async def test_duplicate_non_event_connection_is_rejected(
        self, conn: AsyncConnection, uid
    ) -> None:
        """The NULL trap.

        Postgres treats NULLs as distinct in unique indexes, so a single
        constraint over a nullable event_id would permit UNLIMITED duplicates
        for non-event connections. Two partial indexes are required.
        """
        a = await _user(conn, uid, "p3@example.com")
        b = await _user(conn, uid, "p4@example.com")
        low, high = sorted([a, b])
        await _connection(conn, uid, low, high, event_id=None)

        with pytest.raises((IntegrityError, DBAPIError)):
            await _connection(conn, uid, low, high, event_id=None)

    async def test_same_pair_may_connect_again_at_a_different_event(
        self, conn: AsyncConnection, uid
    ) -> None:
        """Meeting the same person at two events is two connections.

        The dedup that matters happens in connection_views via merged_into_id,
        not by refusing the second edge.
        """
        organizer = await _user(conn, uid, "p5@example.com")
        other = await _user(conn, uid, "p6@example.com")
        low, high = sorted([organizer, other])

        events = []
        for code in ("EV1", "EV2"):
            event_id = uid()
            await conn.execute(
                text(
                    "INSERT INTO events (id, created_by, name, code, starts_at,"
                    " ends_at, timezone) VALUES (:id, :by, 'E', :code, now(),"
                    " now() + interval '1 day', 'Europe/Berlin')"
                ),
                {"id": event_id, "by": organizer, "code": code},
            )
            events.append(event_id)

        for event_id in events:
            await _connection(conn, uid, low, high, event_id=event_id)

        assert await conn.scalar(text("SELECT count(*) FROM connections")) == 2
