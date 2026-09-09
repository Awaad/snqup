"""CRM adapters and card links.

The adapter tests exercise the MAPPING, which is the reviewable half and needs
no network. The HTTP half is deliberately NotImplementedError until the product
has a name and OAuth apps exist - a stub that looks like it works is worse than
one that says it does not.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.repository import Tenant
from acme.domains.billing.enums import EntitlementSource, SubjectKind
from acme.domains.billing.models import Entitlement
from acme.domains.cards.schemas import CardCreate, CardLink, CardUpdate
from acme.domains.cards.service import CardsService
from acme.domains.crm.adapter import ContactPayload
from acme.domains.crm.enums import CrmProvider
from acme.domains.crm.providers.google_contacts import GoogleContactsAdapter
from acme.domains.crm.providers.hubspot import HubSpotAdapter
from acme.domains.crm.registry import adapter_for, supported
from acme.domains.identity.models import User, UserProfile

pytestmark = pytest.mark.integration


CONTACT = ContactPayload(
    display_name="Sarah Jones",
    email="sarah@example.com",
    phone="+441234567890",
    company="Acme Corp",
    title="Head of Sales",
    note="wants a demo of the reporting",
    met_context="DevCon, coffee stand",
    tags=["lead", "berlin"],
)


class TestRegistry:
    def test_both_launch_providers_are_registered(self) -> None:
        assert set(supported()) == {
            CrmProvider.GOOGLE_CONTACTS,
            CrmProvider.HUBSPOT,
        }

    def test_unknown_provider_raises_rather_than_returning_none(self) -> None:
        """Returning None would fail three frames later with no clue why."""
        with pytest.raises(ValueError, match="no adapter"):
            adapter_for("salesforce")  # type: ignore[arg-type]


class TestGoogleContactsMapping:
    def test_meeting_context_survives_a_provider_with_no_fields_for_it(
        self,
    ) -> None:
        """Google Contacts has no companies, no deals, no custom properties.

        Context has to go into free text, and losing it would leave a contact
        indistinguishable from one typed by hand - which is the entire value we
        add.
        """
        person = GoogleContactsAdapter().to_person(CONTACT)
        biography = person["biographies"][0]["value"]  # type: ignore[index]

        assert "DevCon, coffee stand" in biography
        assert "wants a demo" in biography
        assert "lead" in biography

    def test_standard_fields_map_to_google_shapes(self) -> None:
        person = GoogleContactsAdapter().to_person(CONTACT)
        assert person["names"] == [{"displayName": "Sarah Jones"}]
        assert person["emailAddresses"] == [{"value": "sarah@example.com"}]
        assert person["organizations"][0]["title"] == "Head of Sales"  # type: ignore[index]

    def test_a_sparse_contact_does_not_emit_empty_sections(self) -> None:
        person = GoogleContactsAdapter().to_person(ContactPayload(display_name="Just A Name"))
        assert "emailAddresses" not in person
        assert "biographies" not in person


class TestHubSpotMapping:
    def test_custom_properties_come_from_the_connection_mapping(self) -> None:
        """Two HubSpot portals name their custom properties differently.

        Hardcoding one writes into the wrong field - or into nothing, which is
        worse because it looks like it worked.
        """
        properties = HubSpotAdapter().to_properties(
            CONTACT, {"note": "how_we_met", "met_context": "first_touch"}
        )
        assert properties["how_we_met"] == "wants a demo of the reporting"
        assert properties["first_touch"] == "DevCon, coffee stand"

    def test_an_unmapped_field_is_dropped_not_guessed(self) -> None:
        """Writing context into whatever property happens to exist is worse
        than not writing it, because nobody notices."""
        properties = HubSpotAdapter().to_properties(CONTACT, {"note": ""})
        assert "wants a demo of the reporting" not in properties.values()

    def test_display_name_splits_into_first_and_last(self) -> None:
        properties = HubSpotAdapter().to_properties(CONTACT, {})
        assert properties["firstname"] == "Sarah"
        assert properties["lastname"] == "Jones"

    def test_a_single_word_name_has_no_lastname(self) -> None:
        """Mononyms exist, and inventing a surname is worse than omitting one."""
        properties = HubSpotAdapter().to_properties(ContactPayload(display_name="Prince"), {})
        assert properties["firstname"] == "Prince"
        assert "lastname" not in properties


class TestNotImplementedIsExplicit:
    async def test_push_says_what_is_missing(self) -> None:
        """Better than a stub returning success: a stub that looks like it
        works would be discovered by a user whose contacts never arrived."""
        for adapter in (GoogleContactsAdapter(), HubSpotAdapter()):
            with pytest.raises(NotImplementedError, match=r"OAuth|name"):
                await adapter.push_contact({}, CONTACT, None)


async def _user(session: AsyncSession, name: str) -> User:
    user = User()
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, auth_subject=f"s-{name}", email=f"{name}@example.com"))
    await session.flush()
    return user


class TestCardLinks:
    async def test_free_tier_allows_two_custom_links(self, session: AsyncSession) -> None:
        user = await _user(session, "lk-a")
        card = await CardsService(session, Tenant.user(user.id)).create(
            CardCreate(
                display_name="X",
                links=[
                    CardLink(label="Portfolio", url="https://example.com/work"),
                    CardLink(label="Deck", url="https://example.com/deck"),
                ],
            )
        )
        assert len(card.links) == 2

    async def test_a_third_link_needs_an_upgrade(self, session: AsyncSession) -> None:
        """Custom links are where the link-in-bio value sits, so that is the
        honest paywall (00-context/pricing.md)."""
        user = await _user(session, "lk-b")
        with pytest.raises(ApiError) as exc:
            await CardsService(session, Tenant.user(user.id)).create(
                CardCreate(
                    display_name="X",
                    links=[
                        CardLink(label=f"L{i}", url=f"https://example.com/{i}") for i in range(3)
                    ],
                )
            )
        assert exc.value.code == "CARD_LINK_LIMIT_REACHED"

    async def test_socials_are_never_capped(self, session: AsyncSession) -> None:
        """Socials are identity. Capping them makes a free card look broken
        rather than free."""
        user = await _user(session, "lk-c")
        card = await CardsService(session, Tenant.user(user.id)).create(
            CardCreate(
                display_name="X",
                socials={f"net{i}": f"handle{i}" for i in range(12)},
            )
        )
        assert len(card.socials) == 12

    async def test_entitlement_uncaps_links(self, session: AsyncSession) -> None:
        user = await _user(session, "lk-d")
        session.add(
            Entitlement(
                subject_kind=SubjectKind.USER,
                subject_id=user.id,
                entitlement_key="link.custom_limit",
                value_int=-1,
                source=EntitlementSource.MANUAL,
            )
        )
        await session.flush()

        card = await CardsService(session, Tenant.user(user.id)).create(
            CardCreate(
                display_name="X",
                links=[CardLink(label=f"L{i}", url=f"https://example.com/{i}") for i in range(10)],
            )
        )
        assert len(card.links) == 10

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "http://example.com/insecure",
        ],
    )
    def test_dangerous_or_insecure_schemes_are_refused(self, url: str) -> None:
        """These render on the UGC domain, so a javascript: or data: URL here
        is stored XSS on the highest-risk surface we have (ADR-0008)."""
        with pytest.raises(ValidationError):
            CardLink(label="Bad", url=url)  # type: ignore[arg-type]

    async def test_links_can_be_replaced_wholesale(self, session: AsyncSession) -> None:
        user = await _user(session, "lk-e")
        service = CardsService(session, Tenant.user(user.id))
        card = await service.create(
            CardCreate(
                display_name="X",
                links=[CardLink(label="Old", url="https://example.com/old")],
            )
        )
        await service.update(
            card.id,
            CardUpdate(links=[CardLink(label="New", url="https://example.com/new")]),
        )
        assert [link["label"] for link in card.links] == ["New"]


class TestOAuthState:
    """The CSRF defence, and the reason it exists.

    Without a bound, verified, single-use state, an attacker completes an
    authorization flow with THEIR CRM and delivers the callback to a victim.
    The victim's account connects to the attacker's CRM and every contact they
    collect is pushed to the attacker — while their app says "connected".
    """

    @pytest.fixture
    async def redis(self):
        import os

        from redis.asyncio import Redis

        client = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        await client.flushdb()
        yield client
        await client.flushdb()
        await client.aclose()

    async def test_a_valid_state_round_trips(self, redis) -> None:
        from acme.domains.crm.oauth import OAuthStateStore

        store = OAuthStateStore(redis)
        state = await store.issue("user-1", CrmProvider.HUBSPOT, "https://cb")

        stored = await store.consume(state, "user-1")
        assert stored["provider"] == "hubspot"

    async def test_state_is_single_use(self, redis) -> None:
        """A leaked callback URL must not be replayable."""
        from acme.domains.crm.oauth import OAuthStateStore

        store = OAuthStateStore(redis)
        state = await store.issue("user-1", CrmProvider.HUBSPOT, "https://cb")
        await store.consume(state, "user-1")

        with pytest.raises(ApiError) as exc:
            await store.consume(state, "user-1")
        assert exc.value.code == "CRM_OAUTH_STATE_INVALID"

    async def test_another_users_state_is_refused(self, redis) -> None:
        """THE ATTACK. A callback belonging to a different account is not a
        mistake to tolerate."""
        from acme.domains.crm.oauth import OAuthStateStore

        store = OAuthStateStore(redis)
        state = await store.issue("attacker", CrmProvider.HUBSPOT, "https://cb")

        with pytest.raises(ApiError) as exc:
            await store.consume(state, "victim")
        assert exc.value.code == "CRM_OAUTH_STATE_INVALID"

    async def test_an_unknown_state_is_refused(self, redis) -> None:
        """Fails CLOSED, unlike rate limiting. This one IS the defence, and
        proceeding without it connects an account to an unverified CRM."""
        from acme.domains.crm.oauth import OAuthStateStore

        with pytest.raises(ApiError):
            await OAuthStateStore(redis).consume("never-issued", "user-1")


class TestCrmConnectionLifecycle:
    async def test_credentials_are_encrypted_at_rest(self, session: AsyncSession) -> None:
        """A CRM refresh token is write access to someone's customer database.
        A database dump alone must not yield working credentials."""
        from cryptography.fernet import Fernet

        from acme.domains.crm.oauth import OAuthTokens, TokenCipher
        from acme.domains.crm.service import CrmService

        user = await _user(session, "crm-a")
        cipher = TokenCipher(Fernet.generate_key().decode())
        connection = await CrmService(session, user.id).connect(
            CrmProvider.HUBSPOT,
            OAuthTokens(access_token="at", refresh_token="rt", expires_at=None),
            cipher,
        )

        assert b"rt" not in connection.credentials
        assert cipher.decrypt(connection.credentials).refresh_token == "rt"

    async def test_reconnecting_replaces_rather_than_duplicates(
        self, session: AsyncSession
    ) -> None:
        """Two live connections would double-write every contact with neither
        obviously wrong."""
        from cryptography.fernet import Fernet

        from acme.domains.crm.oauth import OAuthTokens, TokenCipher
        from acme.domains.crm.service import CrmService

        user = await _user(session, "crm-b")
        cipher = TokenCipher(Fernet.generate_key().decode())
        service = CrmService(session, user.id)
        tokens = OAuthTokens(access_token="at", refresh_token="rt", expires_at=None)

        await service.connect(CrmProvider.HUBSPOT, tokens, cipher)
        await service.connect(CrmProvider.HUBSPOT, tokens, cipher)

        assert len(await service.connections()) == 1

    async def test_field_mapping_is_per_connection(self, session: AsyncSession) -> None:
        """Two HubSpot portals name their custom properties differently."""
        from cryptography.fernet import Fernet

        from acme.domains.crm.oauth import OAuthTokens, TokenCipher
        from acme.domains.crm.service import CrmService

        user = await _user(session, "crm-c")
        cipher = TokenCipher(Fernet.generate_key().decode())
        service = CrmService(session, user.id)
        await service.connect(
            CrmProvider.HUBSPOT,
            OAuthTokens(access_token="at", refresh_token="rt", expires_at=None),
            cipher,
        )

        updated = await service.set_field_mapping(CrmProvider.HUBSPOT, {"note": "how_we_met"})
        assert updated.field_mapping == {"note": "how_we_met"}

    async def test_syncing_an_unconnected_provider_is_refused(self, session: AsyncSession) -> None:
        from acme.domains.crm.service import CrmService

        user = await _user(session, "crm-d")
        with pytest.raises(ApiError) as exc:
            await CrmService(session, user.id).disconnect(CrmProvider.HUBSPOT)
        assert exc.value.code == "CRM_NOT_CONNECTED"


class TestSyncSafety:
    async def test_a_missing_credential_key_refuses_loudly(self) -> None:
        """Refusing beats storing a CRM token in the clear."""
        from acme.domains.crm.service import CrmSyncService

        with pytest.raises(ValueError, match="CRM_TOKEN_KEY"):
            CrmSyncService(None, "")  # type: ignore[arg-type]

    async def test_a_connection_needing_reauth_is_not_retried(self, session: AsyncSession) -> None:
        """Retrying a revoked grant forever produces a queue that never drains
        and a user who is never told their sync broke."""
        from cryptography.fernet import Fernet

        from acme.domains.crm.oauth import OAuthTokens, TokenCipher
        from acme.domains.crm.service import CrmService, CrmSyncService

        key = Fernet.generate_key().decode()
        user = await _user(session, "crm-e")
        connection = await CrmService(session, user.id).connect(
            CrmProvider.HUBSPOT,
            OAuthTokens(access_token="at", refresh_token="rt", expires_at=None),
            TokenCipher(key),
        )
        connection.needs_reauth = True
        await session.flush()

        assert await CrmSyncService(session, key).push(connection.id, []) == 0

    async def test_a_deleted_connection_pushes_nothing(self, session: AsyncSession) -> None:
        from datetime import UTC, datetime

        from cryptography.fernet import Fernet

        from acme.domains.crm.oauth import OAuthTokens, TokenCipher
        from acme.domains.crm.service import CrmService, CrmSyncService

        key = Fernet.generate_key().decode()
        user = await _user(session, "crm-f")
        connection = await CrmService(session, user.id).connect(
            CrmProvider.HUBSPOT,
            OAuthTokens(access_token="at", refresh_token="rt", expires_at=None),
            TokenCipher(key),
        )
        connection.deleted_at = datetime.now(UTC)
        await session.flush()

        assert await CrmSyncService(session, key).push(connection.id, []) == 0
