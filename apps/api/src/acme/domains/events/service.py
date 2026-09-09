"""Connections service.

Owns the `connections` and `connection_views` tables. The exchange domain
orchestrates but does not write here directly - that would put the same tables
under two owners (ADR-0025).
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.ids import new_id
from acme.core.repository import Tenant
from acme.domains.billing.service import EntitlementsService, Subject
from acme.domains.connections.enums import ConnectionState, ScanChannel
from acme.domains.connections.models import Connection, ConnectionView
from acme.domains.connections.repository import (
    ConnectionEdgeRepository,
    ConnectionViewRepository,
)
from acme.domains.connections.schemas import (
    ConnectionListOut,
    ConnectionOut,
    ConnectionUpdate,
    CounterpartCard,
)


def order_pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    """Canonical ordering.

    `connections_ordered` is a CHECK constraint, so an unordered insert is a
    database error rather than a silently duplicated pair. Ordering here means
    the same two people always produce the same row regardless of who scanned.
    """
    return (a, b) if a < b else (b, a)


class ConnectionsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_existing(
        self, user_a: UUID, user_b: UUID, event_id: UUID | None
    ) -> Connection | None:
        low, high = order_pair(user_a, user_b)
        stmt = select(Connection).where(
            Connection.user_low_id == low,
            Connection.user_high_id == high,
            Connection.deleted_at.is_(None),
        )
        # NULL event_id is a distinct scope, not a wildcard: meeting the same
        # person at an event and again outside one is two connections, matching
        # the two partial unique indexes (ADR-0003).
        stmt = (
            stmt.where(Connection.event_id == event_id)
            if event_id is not None
            else stmt.where(Connection.event_id.is_(None))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def record(
        self,
        *,
        user_a: UUID,
        card_a_id: UUID,
        snapshot_a: dict[str, object],
        user_b: UUID,
        card_b_id: UUID,
        snapshot_b: dict[str, object],
        channel: ScanChannel,
        occurred_at: datetime,
        event_id: UUID | None,
        state: ConnectionState,
    ) -> Connection:
        """Write the edge and both views, in one call.

        Both views are always created, even for a one-way static scan. The
        owner's view is what surfaces the pending request; without it they
        would have no record that anyone scanned them.
        """
        low, high = order_pair(user_a, user_b)
        low_is_a = low == user_a

        connection = Connection(
            id=new_id(),
            user_low_id=low,
            user_high_id=high,
            card_low_id=card_a_id if low_is_a else card_b_id,
            card_high_id=card_b_id if low_is_a else card_a_id,
            card_low_snapshot=snapshot_a if low_is_a else snapshot_b,
            card_high_snapshot=snapshot_b if low_is_a else snapshot_a,
            channel=channel,
            occurred_at=occurred_at,
            event_id=event_id,
            state=state,
        )
        self._session.add(connection)
        await self._session.flush()

        for user_id in (low, high):
            self._session.add(ConnectionView(connection_id=connection.id, user_id=user_id))
        await self._session.flush()
        return connection

    async def view_for(self, connection_id: UUID, user_id: UUID) -> ConnectionView | None:
        stmt = select(ConnectionView).where(
            ConnectionView.connection_id == connection_id,
            ConnectionView.user_id == user_id,
            ConnectionView.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()


class ConnectionMaintenance:
    """Bulk operations the job runner needs.

    Lives here because `connections` and `connection_views` belong to this
    domain. Workers ask for the work to be done; they do not query these tables
    themselves.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def due_reminders(self, at: datetime, limit: int = 500) -> list[ConnectionView]:
        stmt = (
            select(ConnectionView)
            .where(ConnectionView.reminder_at.is_not(None))
            .where(ConnectionView.reminder_at <= at)
            .where(ConnectionView.reminder_done_at.is_(None))
            .where(ConnectionView.deleted_at.is_(None))
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def mark_reminder_sent(self, view: ConnectionView, at: datetime) -> None:
        """Marked in the SAME transaction as the notification.

        Marking first loses the reminder on a crash; marking after sends it
        twice on a retry.
        """
        view.reminder_done_at = at

    async def pending_older_than(self, at: datetime, limit: int = 200) -> list[Connection]:
        stmt = (
            select(Connection)
            .where(Connection.state == ConnectionState.PENDING)
            .where(Connection.deleted_at.is_(None))
            .where(Connection.created_at <= at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def digest_counts(self, event_id: UUID, user_id: UUID) -> dict[str, int]:
        """What one person got out of an event.

        `without_notes` is the actionable half - it turns the digest from a
        summary into a prompt.
        """
        views = list(
            (
                await self._session.execute(
                    select(ConnectionView)
                    .join(Connection, Connection.id == ConnectionView.connection_id)
                    .where(
                        Connection.event_id == event_id,
                        ConnectionView.user_id == user_id,
                        ConnectionView.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        return {
            "connections": len(views),
            "without_notes": sum(1 for v in views if not v.note),
        }

    async def purge_for_user(self, user_id: UUID) -> int:
        """Remove the erased user's own views.

        Their counterpart's views survive - deletion is asymmetric, and one
        person's erasure must not destroy another's record of a meeting that
        happened (ADR-0003).
        """
        from sqlalchemy import delete

        result = await self._session.execute(
            delete(ConnectionView).where(ConnectionView.user_id == user_id)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class EventConnectionStats:
    """Aggregate counts for one event.

    Lives here because `connections` and `anonymous_scans` belong to this
    domain. Events asks for numbers; it does not query these tables itself.

    Returns raw counts only - suppression below the minimum cohort is the
    events domain's decision, since it is the one that knows how many attendees
    there are.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_event(self, event_id: UUID) -> tuple[int, int, int]:
        """(connections, unique connectors, anonymous scans)."""
        from sqlalchemy import func

        from acme.domains.connections.models import AnonymousScan

        connections = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Connection)
                    .where(
                        Connection.event_id == event_id,
                        Connection.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )
        pairs = (
            await self._session.execute(
                select(Connection.user_low_id, Connection.user_high_id).where(
                    Connection.event_id == event_id,
                    Connection.deleted_at.is_(None),
                )
            )
        ).all()
        unique = len({u for pair in pairs for u in pair})
        anonymous = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(AnonymousScan)
                    .where(AnonymousScan.event_id == event_id)
                )
            ).scalar_one()
        )
        return connections, unique, anonymous


class ConnectionListService:
    """The caller's contact list.

    Separate from ConnectionsService, which the exchange uses to WRITE. This
    one only ever reads and mutates the caller's own views, so it is
    tenant-scoped throughout.
    """

    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self._session = session
        self._user_id = user_id
        self._views = ConnectionViewRepository(session, Tenant.user(user_id))
        self._edges = ConnectionEdgeRepository(session)
        self._entitlements = EntitlementsService(session)

    async def page(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        event_id: UUID | None = None,
        tag: str | None = None,
        include_archived: bool = False,
    ) -> ConnectionListOut:
        """One page of the caller's contacts.

        Named `page`, not `list`: a method called `list` shadows the builtin
        inside the class body, so any later `list[UUID]` annotation resolves to
        the method and fails to typecheck. Worth knowing because the error
        message ("Function ... is not valid as a type") points nowhere near the
        cause.

        Connection HISTORY is never gated (00-context/pricing.md).

        Hiding contacts someone already made reads as theft and would generate
        more one-star reviews than every other issue combined. Only export,
        analytics and the reminder cap are paid.
        """
        stmt = self._views.feed(include_archived=include_archived)
        if cursor is not None:
            stmt = stmt.where(ConnectionView.id < _decode_cursor(cursor))
        if tag is not None:
            stmt = stmt.where(ConnectionView.tags.contains([tag]))

        # One extra row tells us whether another page exists without a count.
        rows = list((await self._session.execute(stmt.limit(limit + 1))).scalars())
        has_more = len(rows) > limit
        rows = rows[:limit]

        edges = await self._edges.by_ids([r.connection_id for r in rows])
        if event_id is not None:
            rows = [r for r in rows if edges[r.connection_id].event_id == event_id]

        items = [self._present(view, edges[view.connection_id]) for view in rows]
        return ConnectionListOut(
            items=items,
            next_cursor=_encode_cursor(rows[-1].id) if has_more and rows else None,
        )

    def _present(self, view: ConnectionView, edge: Connection) -> ConnectionOut:
        """Render one row from the caller's side of the edge.

        The snapshot chosen is the OTHER party's, which is the whole point:
        a connection shows you who you met, as they were at the time
        (ADR-0004).
        """
        counterpart_snapshot = (
            edge.card_high_snapshot if edge.user_low_id == self._user_id else edge.card_low_snapshot
        )
        return ConnectionOut(
            id=view.id,
            connection_id=edge.id,
            state=edge.state,
            channel=edge.channel,
            occurred_at=edge.occurred_at,
            event_id=edge.event_id,
            counterpart=CounterpartCard.model_validate(counterpart_snapshot),
            note=view.note,
            note_conflict=view.note_conflict,
            tags=list(view.tags),
            reminder_at=view.reminder_at,
            reminder_done_at=view.reminder_done_at,
            archived_at=view.archived_at,
            merged_into_id=view.merged_into_id,
            created_at=view.created_at,
        )

    async def get(self, view_id: UUID) -> ConnectionOut:
        view = await self._views.get(view_id)
        if view is None:
            # Same code whether it does not exist or belongs to someone else.
            # Distinguishing them confirms the connection exists to a stranger.
            raise ApiError("CONNECTION_NOT_FOUND", status_code=404)
        edges = await self._edges.by_ids([view.connection_id])
        return self._present(view, edges[view.connection_id])

    async def update(self, view_id: UUID, payload: ConnectionUpdate) -> ConnectionOut:
        view = await self._views.get(view_id)
        if view is None:
            raise ApiError("CONNECTION_NOT_FOUND", status_code=404)

        changes = payload.model_dump(exclude_unset=True)

        if "reminder_at" in changes and changes["reminder_at"] is not None:
            await self._assert_reminder_allowed(view)

        if "note" in changes:
            view.note = changes["note"]
            view.note_updated_at = datetime.now(UTC)
            if payload.resolve_note_conflict:
                view.note_conflict = None

        if "tags" in changes and changes["tags"] is not None:
            # Deduplicated and ordered so two clients writing the same set do
            # not produce a spurious difference on the next sync.
            view.tags = sorted(set(changes["tags"]))

        if "reminder_at" in changes:
            view.reminder_at = changes["reminder_at"]
            view.reminder_done_at = None

        if changes.get("archived") is True:
            view.archived_at = datetime.now(UTC)
        elif changes.get("archived") is False:
            view.archived_at = None

        await self._session.flush()
        edges = await self._edges.by_ids([view.connection_id])
        return self._present(view, edges[view.connection_id])

    async def _assert_reminder_allowed(self, view: ConnectionView) -> None:
        """The free tier caps ACTIVE reminders at 3.

        Capped rather than removed, because a free user has to experience the
        feature working before they will pay to uncap it
        (00-context/pricing.md).
        """
        if view.reminder_at is not None and view.reminder_done_at is None:
            return  # replacing an existing reminder, not adding one

        limit = await self._entitlements.limit(
            Subject.user(self._user_id), "connection.reminder_limit"
        )
        if limit == -1:
            return
        if await self._views.active_reminder_count() >= limit:
            raise ApiError(
                "CONNECTION_REMINDER_LIMIT_REACHED",
                status_code=403,
                message=f"plan allows {limit} active reminders",
                details={"limit": limit},
            )

    async def delete(self, view_id: UUID) -> None:
        """Per-view soft delete.

        The EDGE survives until both participants delete their view. Both
        expect asymmetric deletion and GDPR requires it: my removing a contact
        must not erase your record of the same meeting (ADR-0003).
        """
        view = await self._views.get(view_id)
        if view is None:
            raise ApiError("CONNECTION_NOT_FOUND", status_code=404)
        view.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def merge(self, target_id: UUID, source_ids: list[UUID]) -> ConnectionOut:
        """Fold duplicate views into one contact.

        Meeting the same person at three events is one contact with three event
        tags, not three contacts. Only the caller's VIEWS merge - the edges
        stay, because each records a real meeting that happened.
        """
        target = await self._views.get(target_id)
        if target is None:
            raise ApiError("CONNECTION_NOT_FOUND", status_code=404)

        target_pair = await self._edges.participants_of(target.connection_id)
        merged_tags = set(target.tags)
        notes = [target.note] if target.note else []

        for source_id in source_ids:
            if source_id == target_id:
                raise ApiError(
                    "CONNECTION_MERGE_INVALID",
                    status_code=422,
                    message="cannot merge a connection into itself",
                )
            source = await self._views.get(source_id)
            if source is None:
                raise ApiError("CONNECTION_NOT_FOUND", status_code=404)

            source_pair = await self._edges.participants_of(source.connection_id)
            if source_pair != target_pair:
                # Merging two different people into one contact silently loses
                # one of them, and the user would not find out until they went
                # looking for someone who is no longer there.
                raise ApiError(
                    "CONNECTION_MERGE_INVALID",
                    status_code=422,
                    message="connections are with different people",
                )

            merged_tags.update(source.tags)
            if source.note:
                notes.append(source.note)
            source.merged_into_id = target.id

        target.tags = sorted(merged_tags)
        if len(notes) > 1:
            # Concatenated, never discarded. Notes are the highest-value
            # user-authored data here and losing one to a merge is
            # unacceptable (ADR-0016).
            target.note = "\n\n---\n\n".join(notes)
            target.note_updated_at = datetime.now(UTC)

        await self._session.flush()
        edges = await self._edges.by_ids([target.connection_id])
        return self._present(target, edges[target.connection_id])

    async def find_duplicates(self) -> dict[UUID, list[UUID]]:
        """Group the caller's views by counterpart.

        Surfaces "you have met this person 3 times" so the client can offer a
        merge, rather than doing it automatically - two meetings at different
        events are legitimately two records until the user says otherwise.
        """
        views = list((await self._session.execute(self._views.feed())).scalars())
        counterparts = await self._edges.counterpart_ids(
            self._user_id, [v.connection_id for v in views]
        )
        grouped: dict[UUID, list[UUID]] = {}
        for view in views:
            other = counterparts.get(view.connection_id)
            if other is not None:
                grouped.setdefault(other, []).append(view.id)
        return {k: v for k, v in grouped.items() if len(v) > 1}


def _encode_cursor(view_id: UUID) -> str:
    return view_id.hex


def _decode_cursor(cursor: str) -> UUID:
    try:
        return UUID(hex=cursor)
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", status_code=422, message="malformed cursor") from exc
