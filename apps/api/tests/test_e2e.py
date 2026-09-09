"""End-to-end, through the real HTTP stack.

Every other test calls services directly. These go through the ASGI app:
middleware, dependencies, auth, serialisation, error envelopes. That layer has
its own failure modes - a middleware that eats the request body, a dependency
that never runs, a response model that drops a field - and none of them are
visible from a service-level test.

The flow under test is the product: two people meet, exchange, and one of them
does something with it afterwards.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.ids import new_id
from acme.main import create_app

pytestmark = pytest.mark.integration

ISSUER = "https://idp.test"
AUDIENCE = "authenticated"


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


PRIVATE, PUBLIC = _keypair()


def token_for(subject: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=15),
        },
        PRIVATE,
        algorithm="RS256",
    )


@pytest_asyncio.fixture
async def client(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, with the test's transaction-scoped session injected.

    Overriding get_session rather than letting the app open its own is what
    keeps every E2E test rolled back - otherwise these would leave rows behind
    and the suite would stop being order-independent.
    """
    from acme.api.deps import get_session
    from acme.core.auth import JwtVerifier
    from acme.core.config import get_settings

    monkeypatch.setenv("JWT_ISSUER", ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", AUDIENCE)
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    # Static-key verifier: a JWKS fetch would need a live IdP.
    app.state.verifier = JwtVerifier(public_keys=[PUBLIC], issuer=ISSUER, audience=AUDIENCE)
    app.state.session_factory = lambda: _NullSessionFactory(session)
    app.state.redis = _NullRedis()
    app.state.settings = get_settings()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http

    get_settings.cache_clear()


class _NullSessionFactory:
    """Hands back the test's session so /health/ready works without opening a
    second connection outside the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None

    def __call__(self) -> "_NullSessionFactory":
        return self


class _NullRedis:
    """Idempotency and rate limiting FAIL OPEN, so a Redis that raises is a
    valid environment and exercises that path rather than avoiding it."""

    async def set(self, *args: object, **kwargs: object) -> bool:
        raise ConnectionError("no redis in this test")

    async def get(self, *args: object) -> None:
        raise ConnectionError("no redis in this test")

    async def delete(self, *args: object) -> None:
        raise ConnectionError("no redis in this test")

    async def ping(self) -> bool:
        raise ConnectionError("no redis in this test")


async def _register(
    client: httpx.AsyncClient, session: AsyncSession, name: str
) -> tuple[str, dict[str, str]]:
    """Provision a user the way sign-in does, then act as them over HTTP."""
    from acme.domains.identity.service import IdentityService

    subject = f"e2e-{name}-{new_id().hex[:8]}"
    await IdentityService(session).provision(
        auth_subject=subject, email=f"{name}-{new_id().hex[:6]}@example.com"
    )
    await session.flush()
    return subject, {"Authorization": f"Bearer {token_for(subject)}"}


class TestHealth:
    async def test_liveness_touches_nothing(self, client: httpx.AsyncClient) -> None:
        """/health must not touch a dependency, or a database blip restarts the
        process and uptime monitoring pages for the wrong thing."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestAuthentication:
    async def test_an_unauthenticated_request_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/cards")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"

    async def test_the_error_envelope_carries_a_request_id(self, client: httpx.AsyncClient) -> None:
        """One id maps a user report to a trace across three runtimes."""
        response = await client.get("/v1/cards")
        assert response.json()["error"]["request_id"]
        assert response.headers["X-Request-ID"]

    async def test_a_token_from_another_key_is_refused(self, client: httpx.AsyncClient) -> None:
        other_private, _ = _keypair()
        forged = jwt.encode(
            {
                "sub": "attacker",
                "iss": ISSUER,
                "aud": AUDIENCE,
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            other_private,
            algorithm="RS256",
        )
        response = await client.get("/v1/cards", headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401


class TestTheProductFlow:
    """Two people meet, exchange, and follow up. This is the product."""

    async def test_full_exchange_and_follow_up(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        _, alice = await _register(client, session, "alice")
        _, bob = await _register(client, session, "bob")

        # Each creates a card.
        alice_card = await client.post(
            "/v1/cards",
            json={"display_name": "Alice Smith", "company": "Acme Corp"},
            headers=alice,
        )
        assert alice_card.status_code == 201, alice_card.text
        bob_card = await client.post(
            "/v1/cards",
            json={"display_name": "Bob Jones", "company": "Globex"},
            headers=bob,
        )
        assert bob_card.status_code == 201

        # The first card is the default even though nobody asked: a user with
        # cards and no default has nothing to present.
        assert alice_card.json()["is_default"] is True

        # Bob shows his live code.
        token = await client.post(f"/v1/cards/{bob_card.json()['id']}/tokens/live", headers=bob)
        assert token.status_code == 200
        assert token.json()["kind"] == "live"
        assert token.json()["expires_at"] is not None

        # Alice scans it.
        exchange = await client.post(
            "/v1/exchanges",
            json={
                "token": token.json()["token"],
                "card_id": alice_card.json()["id"],
                "channel": "qr_live",
                "note": "wants a demo of the reporting",
            },
            headers=alice | {"Idempotency-Key": str(new_id())},
        )
        assert exchange.status_code == 201, exchange.text
        body = exchange.json()
        # A live token means symmetric: presenting it in-app IS the consent.
        assert body["symmetric"] is True
        assert body["state"] == "confirmed"
        assert body["card"]["display_name"] == "Bob Jones"
        # The public projection must not leak an internal id - a UUIDv7 carries
        # its creation timestamp.
        assert "id" not in body["card"]

        # Both sides see the connection...
        alice_list = await client.get("/v1/connections", headers=alice)
        bob_list = await client.get("/v1/connections", headers=bob)
        assert len(alice_list.json()["items"]) == 1
        assert len(bob_list.json()["items"]) == 1

        # ...each seeing the OTHER person.
        assert alice_list.json()["items"][0]["counterpart"]["display_name"] == "Bob Jones"
        assert bob_list.json()["items"][0]["counterpart"]["display_name"] == "Alice Smith"

        # Alice's note is hers alone. This is the structural guarantee.
        assert alice_list.json()["items"][0]["note"] == "wants a demo of the reporting"
        assert bob_list.json()["items"][0]["note"] is None

        # Alice tags and sets a reminder.
        view_id = alice_list.json()["items"][0]["id"]
        updated = await client.patch(
            f"/v1/connections/{view_id}",
            json={
                "tags": ["lead", "berlin"],
                "reminder_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            },
            headers=alice,
        )
        assert updated.status_code == 200
        assert updated.json()["tags"] == ["berlin", "lead"]

        # Bob cannot reach Alice's connection, even with the right id.
        assert (await client.get(f"/v1/connections/{view_id}", headers=bob)).status_code == 404

    async def test_a_static_scan_is_one_way(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """The harvesting vector this design exists to close.

        A badge can be photographed without the owner knowing, so the scanner
        gets the card and the owner gets a PENDING request.
        """
        _, alice = await _register(client, session, "static-a")
        _, bob = await _register(client, session, "static-b")

        alice_card = (
            await client.post("/v1/cards", json={"display_name": "Alice"}, headers=alice)
        ).json()
        bob_card = (
            await client.post("/v1/cards", json={"display_name": "Bob"}, headers=bob)
        ).json()

        static = await client.get(f"/v1/cards/{bob_card['id']}/tokens/static", headers=bob)
        assert static.json()["expires_at"] is None

        result = await client.post(
            "/v1/exchanges",
            json={
                "token": static.json()["token"],
                "card_id": alice_card["id"],
                "channel": "qr_static",
            },
            headers=alice | {"Idempotency-Key": str(new_id())},
        )
        assert result.status_code == 201
        assert result.json()["symmetric"] is False
        assert result.json()["state"] == "pending"

    async def test_a_replayed_exchange_returns_the_original(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Offline retry makes this the normal case, not an edge case."""
        _, alice = await _register(client, session, "dup-a")
        _, bob = await _register(client, session, "dup-b")

        alice_card = (
            await client.post("/v1/cards", json={"display_name": "A"}, headers=alice)
        ).json()
        bob_card = (await client.post("/v1/cards", json={"display_name": "B"}, headers=bob)).json()
        token = (await client.post(f"/v1/cards/{bob_card['id']}/tokens/live", headers=bob)).json()[
            "token"
        ]

        payload = {"token": token, "card_id": alice_card["id"], "channel": "qr_live"}
        key = {"Idempotency-Key": str(new_id())}

        first = await client.post("/v1/exchanges", json=payload, headers=alice | key)
        second = await client.post("/v1/exchanges", json=payload, headers=alice | key)

        assert first.json()["connection_id"] == second.json()["connection_id"]
        assert second.json()["duplicate"] is True

    async def test_scanning_your_own_card_is_refused(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        _, alice = await _register(client, session, "self")
        card = (await client.post("/v1/cards", json={"display_name": "A"}, headers=alice)).json()
        token = (await client.post(f"/v1/cards/{card['id']}/tokens/live", headers=alice)).json()[
            "token"
        ]

        response = await client.post(
            "/v1/exchanges",
            json={"token": token, "card_id": card["id"], "channel": "qr_live"},
            headers=alice | {"Idempotency-Key": str(new_id())},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SCAN_SELF"


class TestPublicSurface:
    async def test_a_stranger_can_resolve_a_scan(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """The majority path. No account, no auth header.

        Most scanners do not have the app, and that is the growth loop rather
        than an edge case.
        """
        _, owner = await _register(client, session, "pub")
        card = (
            await client.post(
                "/v1/cards",
                json={"display_name": "Sarah Jones", "company": "Acme"},
                headers=owner,
            )
        ).json()
        token = (await client.get(f"/v1/cards/{card['id']}/tokens/static", headers=owner)).json()[
            "token"
        ]

        response = await client.post(f"/v1/scan/{token}")
        assert response.status_code == 200
        assert response.json()["card"]["display_name"] == "Sarah Jones"
        # Never the internal id.
        assert "id" not in response.json()["card"]

    async def test_a_revoked_token_stops_resolving(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """The remedy when a badge photograph leaks."""
        _, owner = await _register(client, session, "rot")
        card = (await client.post("/v1/cards", json={"display_name": "S"}, headers=owner)).json()
        old = (await client.get(f"/v1/cards/{card['id']}/tokens/static", headers=owner)).json()[
            "token"
        ]

        await client.post(f"/v1/cards/{card['id']}/tokens/static/rotate", headers=owner)

        response = await client.post(f"/v1/scan/{old}")
        assert response.status_code == 410
        assert response.json()["error"]["code"] == "TOKEN_REVOKED"


class TestFreeTier:
    async def test_the_second_card_needs_an_upgrade(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        _, user = await _register(client, session, "limit")
        await client.post("/v1/cards", json={"display_name": "First"}, headers=user)

        response = await client.post("/v1/cards", json={"display_name": "Second"}, headers=user)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CARD_LIMIT_REACHED"
        # The client needs the number to render the upgrade prompt.
        assert response.json()["error"]["details"]["limit"] == 1


class TestPrivacy:
    async def test_export_is_free_and_machine_readable(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Article 20. CONNECTION_EXPORT_NOT_ENTITLED must never appear here -
        gating a legal right is how a support ticket becomes a regulatory one.
        """
        _, user = await _register(client, session, "gdpr")
        await client.post("/v1/cards", json={"display_name": "Me"}, headers=user)

        response = await client.get("/v1/privacy/export", headers=user)
        assert response.status_code == 200
        assert response.json()["format_version"] == 1
        assert len(response.json()["cards"]) == 1
        assert "attachment" in response.headers["content-disposition"]

    async def test_account_deletion_is_reachable_in_app(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Apple guideline 5.1.1(v). No support contact, no dark patterns."""
        _, user = await _register(client, session, "delete")
        response = await client.request("DELETE", "/v1/privacy/account", headers=user)
        assert response.status_code == 202
        assert response.json()["status"] == "scheduled"
        assert response.json()["grace_days"] == "30"


class TestPublicEventSurface:
    """The gap this closed: `events.slug` was in the schema with a unique index
    and no endpoint used it, so an organizer putting "register at
    example.net/e/devcon" on a slide had nowhere for that link to land.
    """

    async def _event(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        *,
        visibility: str = "public",
        slug: str | None = None,
    ) -> tuple[Any, dict[str, str]]:
        from acme.core.ids import new_id as _new_id
        from acme.domains.events.models import Event
        from acme.domains.identity.enums import OrgRole
        from acme.domains.identity.models import Organization, OrganizationMember

        subject, headers = await _register(client, session, "org")
        from acme.domains.identity.service import IdentityService

        user = await IdentityService(session).current_user(subject)
        org = Organization(name="Acme Events", slug=f"acme-{_new_id().hex[:8]}", is_personal=False)
        session.add(org)
        await session.flush()
        session.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER))
        event = Event(
            organization_id=org.id,
            created_by=user.id,
            name="DevCon Berlin",
            venue="Station",
            code=f"C{_new_id().hex[:8].upper()}",
            slug=slug or f"devcon-{_new_id().hex[:8]}",
            visibility=visibility,
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=8),
            timezone="Europe/Berlin",
        )
        session.add(event)
        await session.flush()
        return event, headers

    async def test_a_stranger_can_view_a_public_event(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        event, _ = await self._event(client, session)

        response = await client.get(f"/v1/events/public/{event.slug}")
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "DevCon Berlin"
        assert body["organization_name"] == "Acme Events"
        assert body["indexable"] is True
        # A code is an invitation. Printing it on a public page would make
        # every private event joinable by anyone who found the URL.
        assert "code" not in body

    async def test_an_unlisted_event_resolves_but_is_not_indexable(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """The difference between "anyone with the link" and "anyone at all"."""
        event, _ = await self._event(client, session, visibility="unlisted")

        response = await client.get(f"/v1/events/public/{event.slug}")
        assert response.status_code == 200
        assert response.json()["indexable"] is False

    async def test_a_private_event_never_resolves_by_slug(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        event, _ = await self._event(client, session, visibility="private")

        response = await client.get(f"/v1/events/public/{event.slug}")
        assert response.status_code == 404

    async def test_a_private_event_still_resolves_by_code(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Holding a code IS the invitation, which is the whole reason a
        private event has one."""
        event, _ = await self._event(client, session, visibility="private")

        response = await client.get(f"/v1/events/code/{event.code}")
        assert response.status_code == 200
        assert response.json()["name"] == "DevCon Berlin"

    async def test_registration_needs_no_account(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """The growth loop applied to events: on the roster before installing
        anything."""
        event, _ = await self._event(client, session)

        response = await client.post(
            f"/v1/events/public/{event.slug}/register",
            json={"email": "stranger@example.com", "display_name": "Stranger"},
        )
        assert response.status_code == 200
        assert response.json()["registered"] is True
        # So a visitor who DOES have the app can deep-link rather than typing.
        assert response.json()["join_code"] == event.code

    async def test_registering_twice_is_not_an_error(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Someone who taps twice, or who was already on the organizer's
        upload, should see success either way."""
        event, _ = await self._event(client, session)
        payload = {"email": "dupe@example.com"}

        first = await client.post(f"/v1/events/public/{event.slug}/register", json=payload)
        second = await client.post(f"/v1/events/public/{event.slug}/register", json=payload)

        assert first.json()["already_registered"] is False
        assert second.json()["already_registered"] is True
        assert second.status_code == 200

    async def test_the_honeypot_returns_success_and_records_nothing(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Returning success so a bot learns nothing about which field gave it
        away."""
        from sqlalchemy import select

        from acme.domains.events.models import EventRosterEntry

        event, _ = await self._event(client, session)
        response = await client.post(
            f"/v1/events/public/{event.slug}/register",
            json={"email": "bot@example.com", "website": "http://spam.example"},
        )
        assert response.status_code == 200
        rows = (
            await session.execute(
                select(EventRosterEntry).where(EventRosterEntry.event_id == event.id)
            )
        ).scalars()
        assert list(rows) == []

    async def test_self_registration_is_distinguishable_from_an_upload(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """ "78% of your attendees connected" means something different against
        an uploaded registration list than against landing-page signups.

        Collapsing them would let the dashboard quietly overstate a number the
        organizer repeats to sponsors.
        """
        from acme.domains.events.service import PublicEventService

        event, _ = await self._event(client, session)
        await client.post(
            f"/v1/events/public/{event.slug}/register",
            json={"email": "self@example.com"},
        )

        counts = await PublicEventService(session).registration_counts(event.id)
        assert counts == {"self_registered": 1}


class TestProvisioning:
    """First sign-in.

    THE BUG THIS COVERS made the product unusable: `provision()` existed and
    nothing called it, so a brand-new Supabase user with a perfectly valid
    token received AUTH_ACCOUNT_DISABLED. Nobody could sign up.

    Every other test in this file called `provision()` directly, which is
    exactly why none of them caught it.
    """

    async def test_a_new_user_is_provisioned_on_first_request(
        self, client: httpx.AsyncClient
    ) -> None:
        subject = f"brand-new-{new_id().hex[:8]}"
        token = jwt.encode(
            {
                "sub": subject,
                "iss": ISSUER,
                "aud": AUDIENCE,
                "email": "newcomer@example.com",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            PRIVATE,
            algorithm="RS256",
        )

        response = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert response.json()["email"] == "newcomer@example.com"

    async def test_the_display_name_comes_from_user_metadata(
        self, client: httpx.AsyncClient
    ) -> None:
        """Supabase puts OAuth profile fields there. It is CLIENT-WRITABLE, so
        it is used for a display name and never for identity."""
        subject = f"named-{new_id().hex[:8]}"
        token = jwt.encode(
            {
                "sub": subject,
                "iss": ISSUER,
                "aud": AUDIENCE,
                "email": "named@example.com",
                "user_metadata": {"full_name": "Sarah Jones"},
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            PRIVATE,
            algorithm="RS256",
        )

        response = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.json()["display_name"] == "Sarah Jones"

    async def test_provisioning_is_idempotent(self, client: httpx.AsyncClient) -> None:
        """Every authenticated request runs this path. A second row per request
        would be catastrophic."""
        subject = f"repeat-{new_id().hex[:8]}"
        token = jwt.encode(
            {
                "sub": subject,
                "iss": ISSUER,
                "aud": AUDIENCE,
                "email": "repeat@example.com",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            PRIVATE,
            algorithm="RS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        first = await client.get("/v1/me", headers=headers)
        second = await client.get("/v1/me", headers=headers)
        assert first.json()["email"] == second.json()["email"]

    async def test_a_token_without_an_email_is_refused(self, client: httpx.AsyncClient) -> None:
        """Supabase issues tokens for phone and anonymous sign-in too, and
        every downstream feature assumes an email. Failing here beats a
        half-usable account."""
        token = jwt.encode(
            {
                "sub": f"phone-{new_id().hex[:8]}",
                "iss": ISSUER,
                "aud": AUDIENCE,
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            PRIVATE,
            algorithm="RS256",
        )

        response = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"

    async def test_an_erased_account_is_not_resurrected(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """The distinction that matters.

        No row at all means a new user. A row with `deleted_at` set means
        someone asked to be deleted, and provisioning would hand them a fresh
        empty profile carrying their old id.
        """
        subject, headers = await _register(client, session, "erased")
        await client.request("DELETE", "/v1/privacy/account", headers=headers)

        response = await client.get("/v1/me", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH_ACCOUNT_DISABLED"


class TestProfile:
    async def test_timezone_round_trips(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """Quiet hours for push are computed in LOCAL time. Without this the
        server falls back to UTC, which is wrong for most of the world."""
        _, headers = await _register(client, session, "tz")

        updated = await client.patch("/v1/me", json={"timezone": "Europe/Berlin"}, headers=headers)
        assert updated.status_code == 200
        assert updated.json()["timezone"] == "Europe/Berlin"

    async def test_patch_leaves_unsent_fields_alone(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        _, headers = await _register(client, session, "patchme")
        await client.patch("/v1/me", json={"display_name": "Original"}, headers=headers)

        result = await client.patch("/v1/me", json={"locale": "de"}, headers=headers)
        assert result.json()["display_name"] == "Original"
        assert result.json()["locale"] == "de"

    async def test_marketing_consent_is_separate_from_transactional(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        """GDPR requires opt-in for marketing. One combined flag means either
        spamming people or being unable to send a password reset."""
        _, headers = await _register(client, session, "consent")

        result = await client.patch("/v1/me", json={"consent_marketing": True}, headers=headers)
        assert result.json()["consent_marketing"] is True
        assert result.json()["consent_transactional"] is True
