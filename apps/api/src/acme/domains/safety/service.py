"""Safety service."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.safety.enums import ReportStatus
from acme.domains.safety.models import Block, Report
from acme.domains.safety.repository import SafetyRepository


class SafetyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SafetyRepository(session)

    def audit(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        subject_kind: str | None = None,
        subject_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Record an action. Every admin operation calls this, no exceptions.

        Exposed on the SERVICE rather than the repository because the admin
        router is an entry point, and entry points talk to services (import
        contract 4). The audit table exists primarily for this consumer, and an
        action that skips it is invisible when a decision is challenged.
        """
        self._repo.record_audit(
            actor_user_id=actor_user_id,
            action=action,
            subject_kind=subject_kind,
            subject_id=subject_id,
            metadata=metadata,
            request_id=request_id,
        )

    async def open_reports(self) -> list[Report]:
        return await self._repo.open_reports()

    async def is_blocked(self, a: UUID, b: UUID) -> bool:
        """Blocks prevent exchange in BOTH directions.

        Checked symmetrically on purpose: a block is only useful if the blocked
        party cannot route around it by being the one who scans.

        The caller must not tell either party who blocked whom - blocks are
        private, and revealing one turns a safety feature into a signal.
        """
        stmt = select(Block).where(
            or_(
                (Block.blocker_user_id == a) & (Block.blocked_user_id == b),
                (Block.blocker_user_id == b) & (Block.blocked_user_id == a),
            )
        )
        return (await self._session.execute(stmt)).first() is not None

    async def resolve_report(self, report_id: UUID, outcome: ReportStatus, note: str) -> None:
        """Close a report with an outcome and a reason.

        The reason is what a decision is defended with when it is challenged,
        so it is stored rather than left in someone's memory.
        """
        report = await self._session.get(Report, report_id)
        if report is None:
            raise ApiError("VALIDATION_FAILED", status_code=404, message="no such report")
        report.status = outcome
        report.resolution_note = note
        report.resolved_at = datetime.now(UTC)
        await self._session.flush()
