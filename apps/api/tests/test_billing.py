"""Billing webhooks and entitlement synchronisation.

The invariant: multiple sources feed ONE entitlements model, and no code
outside this domain knows a payment provider exists (ADR-0009).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.billing.enums import (
    EntitlementSource,
    EntitlementStatus,
    SubjectKind,
)
from acme.domains.billing.models import BillingEvent, Entitlement
from acme.domains.billing.service import EntitlementsService, Subject
from acme.domains.billing.webhooks import (
    BillingWebhookService,
    SubscriptionUpdate,
    verify_stripe_signature,
)
from acme.domains.identity.models import User, UserProfile

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession, name: str) -> User:
    user = User()
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, auth_subject=f"s-{name}", email=f"{name}@example.com"))
    await session.flush()
    return user


def _update(user: User, **overrides: object) -> SubscriptionUpdate:
    defaults: dict[str, object] = {
        "source": EntitlementSource.STRIPE,
        "external_id": "evt_1",
        "source_ref": "sub_1",
        "subject_kind": SubjectKind.USER,
        "subject_id": user.id,
        "plan_key": "consumer_pro",
        "status": EntitlementStatus.ACTIVE,
        "current_period_end": datetime.now(UTC) + timedelta(days=30),
    }
    defaults.update(overrides)
    return SubscriptionUpdate(**defaults)  # type: ignore[arg-type]


class TestIdempotency:
    async def test_a_repeated_delivery_is_recorded_once(self, session: AsyncSession) -> None:
        """Apple's notifications are eventually consistent and occasionally
        duplicated. A handler assuming exactly-once corrupts entitlement state,
        and the corruption is silent - the user simply has the wrong plan.
        """
        service = BillingWebhookService(session)

        assert await service.ingest(EntitlementSource.APPLE, "n_1", {"a": 1}) is True
        assert await service.ingest(EntitlementSource.APPLE, "n_1", {"a": 1}) is False

        rows = list((await session.execute(select(BillingEvent))).scalars())
        assert len(rows) == 1

    async def test_the_same_id_from_two_sources_is_two_events(self, session: AsyncSession) -> None:
        """Provider ids are only unique within a provider."""
        service = BillingWebhookService(session)
        await service.ingest(EntitlementSource.APPLE, "shared", {})
        await service.ingest(EntitlementSource.STRIPE, "shared", {})

        rows = list((await session.execute(select(BillingEvent))).scalars())
        assert len(rows) == 2


class TestEntitlementSync:
    async def test_a_plan_grants_its_entitlements(self, session: AsyncSession) -> None:
        user = await _user(session, "b-a")
        await BillingWebhookService(session).apply(_update(user))

        entitlements = EntitlementsService(session)
        assert await entitlements.limit(Subject.user(user.id), "card.limit") == 5
        assert await entitlements.allowed(Subject.user(user.id), "connection.export")

    async def test_cancellation_revokes_rather_than_deletes(self, session: AsyncSession) -> None:
        """A deleted row leaves no trace of what someone used to have, which is
        the first question support asks."""
        user = await _user(session, "b-b")
        service = BillingWebhookService(session)
        await service.apply(_update(user))
        await service.apply(_update(user, status=EntitlementStatus.EXPIRED))

        # Back to the free tier for authorization...
        assert await EntitlementsService(session).limit(Subject.user(user.id), "card.limit") == 1
        # ...but the history survives.
        rows = list((await session.execute(select(Entitlement))).scalars())
        assert rows and all(r.status is EntitlementStatus.EXPIRED for r in rows)

    async def test_a_refund_is_distinguishable_from_a_lapse(self, session: AsyncSession) -> None:
        """Different conversations, so they must not flatten to one status.

        A lapsed subscription is a renewal problem; a refunded one may be a
        dispute. Support cannot tell them apart if both read REVOKED.
        """
        lapsed = await _user(session, "b-lapse")
        refunded = await _user(session, "b-refund")
        service = BillingWebhookService(session)

        await service.apply(_update(lapsed, source_ref="sub_lapse"))
        await service.apply(
            _update(lapsed, source_ref="sub_lapse", status=EntitlementStatus.EXPIRED)
        )
        await service.apply(_update(refunded, source_ref="sub_refund"))
        await service.apply(
            _update(refunded, source_ref="sub_refund", status=EntitlementStatus.REVOKED)
        )

        async def statuses(user: User) -> set[EntitlementStatus]:
            rows = (
                await session.execute(select(Entitlement).where(Entitlement.subject_id == user.id))
            ).scalars()
            return {r.status for r in rows}

        assert await statuses(lapsed) == {EntitlementStatus.EXPIRED}
        assert await statuses(refunded) == {EntitlementStatus.REVOKED}

    async def test_downgrade_removes_only_the_dropped_capabilities(
        self, session: AsyncSession
    ) -> None:
        user = await _user(session, "b-c")
        service = BillingWebhookService(session)
        await service.apply(
            _update(
                user,
                subject_kind=SubjectKind.USER,
                plan_key="organizer_business",
            )
        )
        await service.apply(_update(user, plan_key="organizer_pro"))

        entitlements = EntitlementsService(session)
        subject = Subject.user(user.id)
        assert await entitlements.limit(subject, "event.attendee_limit") == 500
        # custom_domain is Business-only and must be gone.
        assert await entitlements.allowed(subject, "event.custom_domain") is False

    async def test_two_sources_both_survive_and_highest_wins(self, session: AsyncSession) -> None:
        """Someone subscribes on the web, forgets, subscribes again in the app.

        Both rows stay so support can see the duplicate and refund one; the
        resolver takes the highest so the customer is never worse off
        (ADR-0009).
        """
        user = await _user(session, "b-d")
        service = BillingWebhookService(session)
        await service.apply(_update(user, source=EntitlementSource.STRIPE, source_ref="sub_web"))
        await service.apply(
            _update(
                user,
                source=EntitlementSource.APPLE,
                source_ref="sub_app",
                external_id="evt_2",
                plan_key="consumer_pro",
            )
        )

        rows = list(
            (
                await session.execute(
                    select(Entitlement).where(Entitlement.entitlement_key == "card.limit")
                )
            ).scalars()
        )
        assert {r.source for r in rows} == {
            EntitlementSource.STRIPE,
            EntitlementSource.APPLE,
        }
        assert await EntitlementsService(session).limit(Subject.user(user.id), "card.limit") == 5

    async def test_reapplying_the_same_update_is_stable(self, session: AsyncSession) -> None:
        """Reconciliation replays events; it must not multiply rows."""
        user = await _user(session, "b-e")
        service = BillingWebhookService(session)
        await service.apply(_update(user))
        await service.apply(_update(user))

        rows = list(
            (
                await session.execute(
                    select(Entitlement).where(Entitlement.entitlement_key == "card.limit")
                )
            ).scalars()
        )
        assert len(rows) == 1

    async def test_no_price_is_stored_anywhere(self) -> None:
        """Prices live in App Store Connect and Stripe and are read at render
        time. One here would drift within a release and nobody would notice
        until a customer did (ADR-0009)."""
        from acme.domains.billing.webhooks import PLAN_ENTITLEMENTS

        for plan in PLAN_ENTITLEMENTS.values():
            for key in plan:
                assert "price" not in key
                assert "cost" not in key


class TestSignatureVerification:
    def test_a_valid_signature_passes(self) -> None:
        import hashlib
        import hmac

        secret, payload, ts = "whsec_test", b'{"id":"evt_1"}', "1700000000"
        signature = hmac.new(
            secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256
        ).hexdigest()
        verify_stripe_signature(payload, f"t={ts},v1={signature}", secret)

    def test_a_forged_signature_is_refused(self) -> None:
        with pytest.raises(ApiError) as exc:
            verify_stripe_signature(b'{"id":"evt_1"}', "t=1700000000,v1=deadbeef", "whsec_test")
        assert exc.value.code == "BILLING_WEBHOOK_INVALID"

    def test_a_malformed_header_is_refused(self) -> None:
        with pytest.raises(ApiError):
            verify_stripe_signature(b"{}", "garbage", "whsec_test")

    def test_the_payload_is_part_of_the_signature(self) -> None:
        """Otherwise a valid signature could be replayed onto a different body,
        which is how an attacker grants themselves a plan."""
        import hashlib
        import hmac

        secret, ts = "whsec_test", "1700000000"
        signature = hmac.new(
            secret.encode(), f"{ts}.".encode() + b'{"plan":"free"}', hashlib.sha256
        ).hexdigest()

        with pytest.raises(ApiError):
            verify_stripe_signature(
                b'{"plan":"organizer_business"}', f"t={ts},v1={signature}", secret
            )


class TestReconciliation:
    async def test_unprocessed_deliveries_are_findable(self, session: AsyncSession) -> None:
        """Webhooks get missed and handlers crash. Without this the only
        symptom is a customer who paid and has no access."""
        service = BillingWebhookService(session)
        await service.ingest(EntitlementSource.STRIPE, "evt_a", {})
        await service.ingest(EntitlementSource.STRIPE, "evt_b", {})
        await service.mark_processed(EntitlementSource.STRIPE, "evt_a")

        pending = await service.unprocessed()
        assert [e.external_id for e in pending] == ["evt_b"]
