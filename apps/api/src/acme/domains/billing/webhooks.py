"""Billing webhook ingestion.

Multiple sources feed ONE entitlements model (ADR-0009). Adding Google Play
later must be a new handler and zero changes anywhere else, which is why
nothing outside this module knows a payment provider exists.

The invariant every handler obeys:

    receive -> verify signature -> record in billing_events (unique on
    source + external_id) -> if already present, stop -> process -> mark done

Apple's notifications are eventually consistent and occasionally duplicated. A
handler that assumes exactly-once delivery corrupts entitlement state, and the
corruption is silent: the user simply has the wrong plan.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.billing.enums import (
    EntitlementSource,
    EntitlementStatus,
    SubjectKind,
)
from acme.domains.billing.models import BillingEvent, Entitlement, Subscription

# What each plan grants. The ONLY place a plan maps to capabilities.
#
# Prices are deliberately absent: they live in App Store Connect and Stripe and
# are read at render time. A price here would drift from the store within a
# release and nobody would notice until a customer did.
PLAN_ENTITLEMENTS: dict[str, dict[str, int | bool]] = {
    "consumer_pro": {
        "card.limit": 5,
        "card.custom_fields": True,
        "card.qr_customisation": True,
        "card.remove_branding": True,
        "link.page_limit": 5,
        "link.custom_limit": -1,
        "link.custom_slug": True,
        "analytics.card": True,
        "analytics.link": True,
        "connection.reminder_limit": -1,
        "connection.export": True,
        "connection.crm_sync": True,
    },
    "organizer_pro": {
        "event.attendee_limit": 500,
        "event.dashboard_live": True,
        "event.attendee_export": True,
        "event.attendee_import": True,
        "event.branded_page": True,
        "event.announcements": True,
        "org.seat_limit": 3,
    },
    "organizer_business": {
        "event.attendee_limit": 2500,
        "event.dashboard_live": True,
        "event.attendee_export": True,
        "event.attendee_import": True,
        "event.branded_page": True,
        "event.custom_domain": True,
        "event.announcements": True,
        "org.seat_limit": 10,
    },
}


@dataclass(frozen=True, slots=True)
class SubscriptionUpdate:
    """A provider event, normalised.

    Handlers translate their provider's shape into this; everything downstream
    is provider-agnostic. That translation is the entire integration surface.
    """

    source: EntitlementSource
    external_id: str
    source_ref: str
    subject_kind: SubjectKind
    subject_id: UUID
    plan_key: str
    status: EntitlementStatus
    current_period_end: datetime | None


def verify_stripe_signature(payload: bytes, header: str, secret: str) -> None:
    """Constant-time comparison against the signed payload.

    Rotation is why the secret is checked against a list elsewhere: a new
    endpoint secret must be ACCEPTED before the old one is removed, or rotation
    rejects live webhooks and silently corrupts entitlement state
    (runbooks/secret-rotation.md).
    """
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    timestamp, signature = parts.get("t"), parts.get("v1")
    if not timestamp or not signature:
        raise ApiError("BILLING_WEBHOOK_INVALID", status_code=400)

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ApiError("BILLING_WEBHOOK_INVALID", status_code=400)


class BillingWebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ingest(
        self, source: EntitlementSource, external_id: str, payload: dict[str, object]
    ) -> bool:
        """Record a delivery. Returns False if it was already seen.

        The unique index on (source, external_id) is what makes this safe: two
        concurrent deliveries of the same event cannot both proceed, because
        the second insert fails rather than racing.
        """
        existing = (
            await self._session.execute(
                select(BillingEvent).where(
                    BillingEvent.source == source,
                    BillingEvent.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False

        self._session.add(BillingEvent(source=source, external_id=external_id, payload=payload))
        await self._session.flush()
        return True

    async def apply(self, update: SubscriptionUpdate) -> None:
        """Normalise a provider event into subscription and entitlement rows."""
        subscription = (
            await self._session.execute(
                select(Subscription).where(
                    Subscription.source == update.source,
                    Subscription.source_ref == update.source_ref,
                )
            )
        ).scalar_one_or_none()

        if subscription is None:
            subscription = Subscription(
                subject_kind=update.subject_kind,
                subject_id=update.subject_id,
                source=update.source,
                source_ref=update.source_ref,
                plan_key=update.plan_key,
                status=update.status,
                current_period_end=update.current_period_end,
            )
            self._session.add(subscription)
        else:
            subscription.plan_key = update.plan_key
            subscription.status = update.status
            subscription.current_period_end = update.current_period_end
        await self._session.flush()

        await self._sync_entitlements(subscription, update)

    async def _sync_entitlements(
        self, subscription: Subscription, update: SubscriptionUpdate
    ) -> None:
        """Rewrite this source's entitlements to match the plan.

        Scoped to ONE source. entitlements_active_idx is unique per source, so
        a user holding both an Apple and a Stripe subscription keeps both rows
        and the resolver takes the highest - which is what lets support see the
        duplicate and refund one (ADR-0009).

        Revoked rather than deleted: a deleted row leaves no trace of what
        someone used to have, and that is the first question support asks.
        """
        current = list(
            (
                await self._session.execute(
                    select(Entitlement).where(
                        Entitlement.subject_kind == update.subject_kind,
                        Entitlement.subject_id == update.subject_id,
                        Entitlement.source == update.source,
                        Entitlement.status.in_([EntitlementStatus.ACTIVE, EntitlementStatus.GRACE]),
                    )
                )
            ).scalars()
        )

        active = update.status in {EntitlementStatus.ACTIVE, EntitlementStatus.GRACE}
        wanted = PLAN_ENTITLEMENTS.get(update.plan_key, {}) if active else {}

        for row in current:
            if row.entitlement_key not in wanted:
                # Propagate the SUBSCRIPTION's status rather than always
                # REVOKED. A lapsed subscription (EXPIRED) and a refunded one
                # (REVOKED) are different support conversations, and flattening
                # them loses the only signal telling them apart. A plan
                # downgrade that drops a capability while the subscription is
                # still active is a revocation of that capability.
                row.status = update.status if not active else EntitlementStatus.REVOKED

        by_key = {row.entitlement_key: row for row in current}
        for key, value in wanted.items():
            existing_row = by_key.get(key)
            is_bool = isinstance(value, bool)
            if existing_row is None:
                self._session.add(
                    Entitlement(
                        subject_kind=update.subject_kind,
                        subject_id=update.subject_id,
                        entitlement_key=key,
                        value_int=None if is_bool else int(value),
                        value_bool=bool(value) if is_bool else None,
                        source=update.source,
                        subscription_id=subscription.id,
                        status=update.status,
                        expires_at=update.current_period_end,
                    )
                )
            else:
                existing_row.value_int = None if is_bool else int(value)
                existing_row.value_bool = bool(value) if is_bool else None
                existing_row.status = update.status
                existing_row.expires_at = update.current_period_end
                existing_row.subscription_id = subscription.id

        await self._session.flush()

    async def mark_processed(
        self, source: EntitlementSource, external_id: str, error: str | None = None
    ) -> None:
        event = (
            await self._session.execute(
                select(BillingEvent).where(
                    BillingEvent.source == source,
                    BillingEvent.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if event is None:
            return
        event.processed_at = datetime.now(UTC)
        event.error = error
        await self._session.flush()

    async def unprocessed(self, limit: int = 100) -> list[BillingEvent]:
        """Deliveries that were recorded but never completed.

        The reconciliation job's input. Webhooks get missed and handlers crash;
        without this the only symptom is a customer who paid and has no access.
        """
        stmt = (
            select(BillingEvent)
            .where(BillingEvent.processed_at.is_(None))
            .order_by(BillingEvent.received_at)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())
