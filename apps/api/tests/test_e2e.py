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
