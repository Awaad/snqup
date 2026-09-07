"""Connections repositories."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.repository import TenantScopedRepository
from acme.domains.connections.models import Connection, ConnectionView


class ConnectionViewRepository(TenantScopedRepository[ConnectionView]):
    """The caller's own views. Tenant-scoped, so another user's notes are not
    merely filtered out - they are in rows this repository never selects."""

    model = ConnectionView
    user_column = "user_id"
    organization_column = None

    def feed(self, *, include_archived: bool = False) -> Select[tuple[ConnectionView]]:
        stmt = self.scoped()
        if not include_archived:
            stmt = stmt.where(ConnectionView.archived_at.is_(None))
        # Merged views are folded into their target and must not appear twice.
        stmt = stmt.where(ConnectionView.merged_into_id.is_(None))
        return stmt.order_by(ConnectionView.created_at.desc(), ConnectionView.id.desc())

    async def due_reminders(self, now: datetime) -> list[ConnectionView]:
        stmt = (
            self.scoped()
            .where(ConnectionView.reminder_at.is_not(None))
            .where(ConnectionView.reminder_at <= now)
            .where(ConnectionView.reminder_done_at.is_(None))
            .order_by(ConnectionView.reminder_at)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def active_reminder_count(self) -> int:
        stmt = (
            self.scoped()
            .where(ConnectionView.reminder_at.is_not(None))
            .where(ConnectionView.reminder_done_at.is_(None))
        )
        return len(list((await self.session.execute(stmt)).scalars()))


class ConnectionEdgeRepository:
    """The shared edge. NOT tenant-scoped, because both participants own it.

    Access is always reached through a ConnectionView the caller owns, so this
    is never the entry point for a request - loading an edge without first
    proving the caller is a participant would bypass the whole model.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_ids(self, ids: list[UUID]) -> dict[UUID, Connection]:
        if not ids:
            return {}
        stmt = select(Connection).where(Connection.id.in_(ids))
        return {c.id: c for c in (await self._session.execute(stmt)).scalars()}

    async def participants_of(self, connection_id: UUID) -> tuple[UUID, UUID] | None:
        stmt = select(Connection).where(Connection.id == connection_id)
        edge = (await self._session.execute(stmt)).scalar_one_or_none()
        return (edge.user_low_id, edge.user_high_id) if edge else None

    async def between(self, user_id: UUID, other_id: UUID) -> list[Connection]:
        """Every edge between two people, across all event scopes.

        Used by duplicate detection: meeting someone at three events produces
        three edges, and the merge folds the caller's views into one contact
        while leaving the edges alone - each records a real meeting.
        """
        low, high = sorted([user_id, other_id])
        stmt = select(Connection).where(
            Connection.user_low_id == low,
            Connection.user_high_id == high,
            Connection.deleted_at.is_(None),
        )
        return list((await self._session.execute(stmt)).scalars())

    async def counterpart_ids(self, user_id: UUID, connection_ids: list[UUID]) -> dict[UUID, UUID]:
        if not connection_ids:
            return {}
        stmt = select(Connection).where(
            Connection.id.in_(connection_ids),
            or_(
                Connection.user_low_id == user_id,
                Connection.user_high_id == user_id,
            ),
        )
        return {
            c.id: (c.user_high_id if c.user_low_id == user_id else c.user_low_id)
            for c in (await self._session.execute(stmt)).scalars()
        }
