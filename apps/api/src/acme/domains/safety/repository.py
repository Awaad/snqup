"""Safety repository."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.safety.enums import ReportStatus
from acme.domains.safety.models import AuditLog, Block, Report


class SafetyRepository:
    """NOT tenant-scoped, deliberately.

    Blocks are symmetric, so a lookup spans two users by definition. Reports
    are read by staff acting across tenants. Both are checked by role at the
    service layer rather than filtered by tenant.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def blocks_between(self, a: UUID, b: UUID) -> Block | None:
        stmt = select(Block).where(
            or_(
                (Block.blocker_user_id == a) & (Block.blocked_user_id == b),
                (Block.blocker_user_id == b) & (Block.blocked_user_id == a),
            )
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def blocked_by(self, user_id: UUID) -> list[UUID]:
        stmt = select(Block.blocked_user_id).where(Block.blocker_user_id == user_id)
        return list((await self._session.execute(stmt)).scalars())

    async def open_reports(self, limit: int = 50) -> list[Report]:
        stmt = (
            select(Report)
            .where(Report.status == ReportStatus.OPEN)
            .order_by(Report.created_at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def reports_about_user(self, user_id: UUID) -> list[Report]:
        """Repeat-offender detection.

        Works because report subjects are SET NULL rather than CASCADE:
        suspending a card and later purging it must not erase the record of why
        it was suspended.
        """
        stmt = select(Report).where(Report.subject_user_id == user_id)
        return list((await self._session.execute(stmt)).scalars())

    def record_audit(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        subject_kind: str | None = None,
        subject_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        """Append-only. Every admin action writes here, no exceptions.

        This table exists primarily for the admin console, and an action that
        skips it is invisible when a decision is challenged.
        """
        entry = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            subject_kind=subject_kind,
            subject_id=subject_id,
            audit_metadata=metadata or {},
            request_id=request_id,
        )
        self._session.add(entry)
        return entry
