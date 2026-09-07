"""Connections service.

Owns the `connections` and `connection_views` tables. The exchange domain
orchestrates but does not write here directly - that would put the same tables
under two owners (ADR-0025).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.ids import new_id
from acme.domains.connections.enums import ConnectionState, ScanChannel
from acme.domains.connections.models import Connection, ConnectionView


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
