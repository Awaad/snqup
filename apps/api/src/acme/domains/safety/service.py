"""Safety service."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.safety.models import Block


class SafetyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
