"""Billing repository.

NOT tenant-scoped. Webhooks arrive unauthenticated with a provider's own
identifiers, so there is no tenant at the point of write - the subject is
resolved FROM the payload rather than from a session.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.billing.enums import EntitlementSource, EntitlementStatus
from acme.domains.billing.models import BillingEvent, Entitlement, Subscription


class BillingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def event_by_external_id(
        self, source: EntitlementSource, external_id: str
    ) -> BillingEvent | None:
        stmt = select(BillingEvent).where(
            BillingEvent.source == source,
            BillingEvent.external_id == external_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def subscription_by_ref(
        self, source: EntitlementSource, source_ref: str
    ) -> Subscription | None:
        stmt = select(Subscription).where(
            Subscription.source == source, Subscription.source_ref == source_ref
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def subscriptions_for(self, subject_id: UUID) -> list[Subscription]:
        stmt = select(Subscription).where(Subscription.subject_id == subject_id)
        return list((await self._session.execute(stmt)).scalars())

    async def active_entitlements(
        self, subject_id: UUID, source: EntitlementSource | None = None
    ) -> list[Entitlement]:
        stmt = select(Entitlement).where(
            Entitlement.subject_id == subject_id,
            Entitlement.status.in_([EntitlementStatus.ACTIVE, EntitlementStatus.GRACE]),
        )
        if source is not None:
            stmt = stmt.where(Entitlement.source == source)
        return list((await self._session.execute(stmt)).scalars())

    async def all_entitlement_subjects(self, limit: int = 1000) -> set[UUID]:
        """Every subject that holds an entitlement.

        Orphan DETECTION lives in the reconciliation job, not here: deciding
        whether a subject exists needs the identity domain, and reaching into
        its models from a repository is exactly the coupling the import
        contract forbids.

        subject_id is polymorphic with no foreign key (a deliberate limitation,
        see schema/review-2026-09-05.md), so the database cannot catch orphans
        and something has to.
        """
        stmt = select(Entitlement.subject_id).distinct().limit(limit)
        return set((await self._session.execute(stmt)).scalars())
