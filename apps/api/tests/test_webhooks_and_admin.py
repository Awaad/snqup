"""Webhook routes, CRM OAuth, and the admin surface.

The webhook tests matter because all three providers authenticate differently
and getting any of them wrong turns the endpoint into a public "grant me a
subscription" API that behaves exactly like a working one.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.billing.enums import EntitlementSource, SubjectKind
from acme.domains.billing.service import EntitlementsService
from acme.domains.billing.webhooks import (
    BillingWebhookService,
    google_play_external_id,
    parse_google_play_message,
    verify_google_play_token,
)
from acme.domains.crm.enums import CrmProvider
from acme.domains.crm.oauth import (
    OAuthTokens,
    TokenCipher,
    authorize_url,
    generate_state,
)
from acme.domains.identity.models import User, UserProfile
from acme.domains.identity.service import IdentityService
from acme.domains.safety.service import SafetyService

pytestmark = pytest.mark.integration


class TestGooglePlay:
    """RTDN has NO signature over the body.

    A Pub/Sub OIDC bearer token is the only thing authenticating the request,
    so skipping it leaves a public subscription grant.
    """

    def test_a_missing_bearer_token_is_refused(self) -> None:
        with pytest.raises(ApiError) as exc:
            verify_google_play_token(None, "aud", "sa@example.com")
        assert exc.value.code == "BILLING_WEBHOOK_INVALID"

    def test_a_non_bearer_authorization_is_refused(self) -> None:
        with pytest.raises(ApiError):
            verify_google_play_token("Basic abc123", "aud", "sa@example.com")

    def test_the_pubsub_envelope_is_unwrapped(self) -> None:
        """The notification is base64 INSIDE message.data.

        A handler written against Apple's flat shape sees an empty payload and
        silently processes nothing.
        """
        notification = {
            "packageName": "com.example.app",
            "eventTimeMillis": "1700000000000",
            "subscriptionNotification": {
                "purchaseToken": "tok_123",
                "notificationType": 4,
            },
        }
        envelope = {
            "message": {"data": base64.b64encode(json.dumps(notification).encode()).decode()}
        }
        assert parse_google_play_message(envelope) == notification

    def test_a_missing_envelope_is_refused(self) -> None:
        with pytest.raises(ApiError):
            parse_google_play_message({"notificationType": 4})

    def test_an_undecodable_payload_is_refused(self) -> None:
        with pytest.raises(ApiError):
            parse_google_play_message({"message": {"data": "!!!not-base64!!!"}})

    def test_external_id_is_stable_for_the_same_notification(self) -> None:
        """RTDN carries no event id, and Pub/Sub is at-least-once BY DESIGN.

        Without a derived id, every redelivery reprocesses the same state
        change.
        """
        notification = {
            "packageName": "com.example.app",
            "eventTimeMillis": "1700000000000",
            "subscriptionNotification": {
                "purchaseToken": "tok_123",
                "notificationType": 4,
            },
        }
        assert google_play_external_id(notification) == google_play_external_id(dict(notification))

    def test_different_notifications_get_different_ids(self) -> None:
        base = {
            "packageName": "com.example.app",
            "eventTimeMillis": "1700000000000",
            "subscriptionNotification": {
                "purchaseToken": "tok_123",
                "notificationType": 4,
            },
        }
        renewed = {
            **base,
            "subscriptionNotification": {
                "purchaseToken": "tok_123",
                "notificationType": 2,
            },
        }
        assert google_play_external_id(base) != google_play_external_id(renewed)

    async def test_a_redelivered_notification_is_recorded_once(self, session: AsyncSession) -> None:
        service = BillingWebhookService(session)
        external_id = "com.example.app|tok_123|4|1700000000000"

        assert await service.ingest(EntitlementSource.GOOGLE, external_id, {}) is True
        assert await service.ingest(EntitlementSource.GOOGLE, external_id, {}) is False


class TestCrmOAuth:
    def test_google_requests_offline_access(self) -> None:
        """Without access_type=offline AND prompt=consent, Google returns no
        refresh token and the connection dies an hour after it is made."""
        url = authorize_url(
            CrmProvider.GOOGLE_CONTACTS,
            client_id="cid",
            redirect_uri="https://example.com/cb",
            state="s",
        )
        assert "access_type=offline" in url
        assert "prompt=consent" in url

    def test_state_is_present_and_unguessable(self) -> None:
        """State is a CSRF token, not decoration.

        Without it an attacker can complete a flow that connects THEIR CRM to
        the victim's account, and every contact the victim collects is then
        pushed to the attacker.
        """
        url = authorize_url(
            CrmProvider.HUBSPOT,
            client_id="cid",
            redirect_uri="https://example.com/cb",
            state=generate_state(),
        )
        assert "state=" in url
        assert len({generate_state() for _ in range(100)}) == 100

    def test_tokens_round_trip_through_encryption(self) -> None:
        from cryptography.fernet import Fernet

        cipher = TokenCipher(Fernet.generate_key().decode())
        tokens = OAuthTokens(
            access_token="at",
            refresh_token="rt",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        assert cipher.decrypt(cipher.encrypt(tokens)).refresh_token == "rt"

    def test_a_missing_key_refuses_to_store_plaintext(self) -> None:
        """A CRM refresh token is write access to someone's customer database.
        Failing loudly beats storing it in the clear."""
        with pytest.raises(ValueError, match="CRM_TOKEN_KEY"):
            TokenCipher("")

    def test_tampered_ciphertext_is_refused(self) -> None:
        """Fernet is authenticated, so a modified blob fails rather than
        decrypting to garbage that gets sent to a provider as a credential."""
        from cryptography.fernet import Fernet

        from acme.domains.crm.adapter import CrmAuthError

        cipher = TokenCipher(Fernet.generate_key().decode())
        blob = bytearray(
            cipher.encrypt(OAuthTokens(access_token="at", refresh_token="rt", expires_at=None))
        )
        blob[-5] ^= 0xFF
        with pytest.raises(CrmAuthError):
            cipher.decrypt(bytes(blob))

    def test_a_token_near_expiry_is_refreshed_early(self) -> None:
        """A token expiring mid-request fails a sync the user triggered and
        looks like a broken integration."""
        soon = OAuthTokens(
            access_token="at",
            refresh_token="rt",
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )
        later = OAuthTokens(
            access_token="at",
            refresh_token="rt",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
        assert soon.needs_refresh is True
        assert later.needs_refresh is False


class TestAdminSurface:
    async def test_search_masks_emails(self, session: AsyncSession) -> None:
        """Staff browsing a contact database is exactly the risk this product
        carries, so a list view identifies an account and no more."""
        user = User()
        session.add(user)
        await session.flush()
        session.add(
            UserProfile(user_id=user.id, auth_subject="s1", email="sarah.jones@example.com")
        )
        await session.flush()

        rows = await IdentityService(session).admin_search(email=None, limit=10)
        assert rows
        masked = str(rows[0]["email_masked"])
        assert masked.startswith("sa")
        assert "sarah.jones" not in masked

    async def test_an_erased_user_shows_as_erased_not_blank(self, session: AsyncSession) -> None:
        """Support needs to tell "erased" from "never had a profile"."""
        user = User()
        session.add(user)
        await session.flush()

        rows = await IdentityService(session).admin_search(email=None, limit=10)
        assert any(r["email_masked"] == "erased" for r in rows)

    async def test_suspension_revokes_tokens_and_keeps_connections(
        self, session: AsyncSession
    ) -> None:
        """The counterpart did nothing wrong. Destroying their record of a
        meeting that happened would punish them for someone else's abuse."""
        from acme.core.repository import Tenant
        from acme.domains.cards.schemas import CardCreate
        from acme.domains.cards.service import (
            AdminCardService,
            CardsService,
            TokenResolver,
        )

        owner = User()
        session.add(owner)
        await session.flush()
        session.add(UserProfile(user_id=owner.id, auth_subject="s-own", email="own@example.com"))
        await session.flush()

        service = CardsService(session, Tenant.user(owner.id))
        card = await service.create(CardCreate(display_name="Phisher"))
        token = (await service.mint_static_token(card.id)).token

        await AdminCardService(session).suspend(card.id)

        with pytest.raises(ApiError) as exc:
            await TokenResolver(session).resolve(token)
        # Indistinguishable from a card that never existed
        # (runbooks/abuse-takedown.md).
        assert exc.value.code in {"TOKEN_NOT_FOUND", "TOKEN_REVOKED"}

    async def test_every_admin_action_is_auditable(self, session: AsyncSession) -> None:
        """The audit table exists primarily for this consumer, and an action
        that skips it is invisible when a decision is challenged."""
        from sqlalchemy import select

        from acme.domains.safety.models import AuditLog

        staff = User()
        session.add(staff)
        await session.flush()

        SafetyService(session).audit(
            actor_user_id=staff.id,
            action="admin.card.suspend",
            subject_kind="card",
            metadata={"reason": "phishing"},
        )
        await session.flush()

        entry = (await session.execute(select(AuditLog))).scalar_one()
        assert entry.action == "admin.card.suspend"
        assert entry.audit_metadata["reason"] == "phishing"


class TestManualGrants:
    async def test_a_comped_entitlement_resolves_like_a_paid_one(
        self, session: AsyncSession
    ) -> None:
        """Same table, same resolver, no special-case code."""
        from acme.domains.billing.service import Subject

        user = User()
        session.add(user)
        await session.flush()

        await EntitlementsService(session).grant_manual(
            subject_kind=SubjectKind.USER,
            subject_id=user.id,
            key="card.limit",
            value_int=5,
            value_bool=None,
            expires_at=(datetime.now(UTC) + timedelta(days=90)).isoformat(),
            source=EntitlementSource.MANUAL,
        )

        assert await EntitlementsService(session).limit(Subject.user(user.id), "card.limit") == 5

    async def test_a_grant_needs_exactly_one_value(self, session: AsyncSession) -> None:
        user = User()
        session.add(user)
        await session.flush()

        with pytest.raises(ValueError, match="exactly one"):
            await EntitlementsService(session).grant_manual(
                subject_kind=SubjectKind.USER,
                subject_id=user.id,
                key="card.limit",
                value_int=5,
                value_bool=True,
                expires_at=datetime.now(UTC).isoformat(),
                source=EntitlementSource.MANUAL,
            )
