"""Authentication.

Uses real RS256 keys generated per test rather than a stubbed verifier. A
stubbed verifier tests that our code calls a function; these test that a token
someone could actually present is accepted or rejected for the right reason.
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.auth import JwtVerifier
from acme.core.errors import ApiError
from acme.domains.identity.service import IdentityService

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


def _token(private: str, *, sub: str = "user-1", **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": sub,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256")


class TestJwtVerification:
    """Verification is async because the real verifier fetches JWKS.

    Keeping it sync would force the JWKS verifier to block the event loop on
    every cache miss, which at startup is every request at once.
    """

    async def test_valid_token_is_accepted(self) -> None:
        private, public = _keypair()
        verifier = JwtVerifier(public_keys=[public], issuer=ISSUER, audience=AUDIENCE)
        assert (await verifier.verify(_token(private)))["sub"] == "user-1"

    async def test_expired_token_is_rejected(self) -> None:
        private, public = _keypair()
        verifier = JwtVerifier(public_keys=[public], issuer=ISSUER, audience=AUDIENCE)
        expired = _token(private, exp=datetime.now(UTC) - timedelta(minutes=1))
        with pytest.raises(ApiError) as exc:
            await verifier.verify(expired)
        assert exc.value.code == "AUTH_TOKEN_INVALID"

    async def test_token_signed_by_another_key_is_rejected(self) -> None:
        """The attack: a well-formed token from the wrong issuer's key."""
        attacker_private, _ = _keypair()
        _, our_public = _keypair()
        verifier = JwtVerifier(public_keys=[our_public], issuer=ISSUER, audience=AUDIENCE)
        with pytest.raises(ApiError):
            await verifier.verify(_token(attacker_private))

    async def test_wrong_audience_is_rejected(self) -> None:
        private, public = _keypair()
        verifier = JwtVerifier(public_keys=[public], issuer=ISSUER, audience=AUDIENCE)
        with pytest.raises(ApiError):
            await verifier.verify(_token(private, aud="some-other-app"))

    async def test_rotation_accepts_both_keys(self) -> None:
        """Why public_keys is a list and not a single value.

        Rotation is two-phase: the new key must be ACCEPTED before it starts
        signing (runbooks/secret-rotation.md). A single-key field would make
        every rotation an outage for the length of the longest token TTL.
        """
        old_private, old_public = _keypair()
        new_private, new_public = _keypair()
        verifier = JwtVerifier(
            public_keys=[old_public, new_public], issuer=ISSUER, audience=AUDIENCE
        )
        assert (await verifier.verify(_token(old_private, sub="a")))["sub"] == "a"
        assert (await verifier.verify(_token(new_private, sub="b")))["sub"] == "b"


class TestIdentityResolution:
    async def test_provision_creates_anchor_and_profile_together(
        self, session: AsyncSession
    ) -> None:
        """Never one without the other.

        An anchor with no profile is indistinguishable from an erased account,
        so a partial write would lock a user out of an account they just made.
        """
        service = IdentityService(session)
        user = await service.provision(auth_subject="sub-new", email="new@example.com")
        assert user.email == "new@example.com"
        assert (await service.current_user("sub-new")).id == user.id

    async def test_provision_is_idempotent(self, session: AsyncSession) -> None:
        """Sign-in races and retries must not create two accounts."""
        service = IdentityService(session)
        first = await service.provision(auth_subject="s", email="a@example.com")
        second = await service.provision(auth_subject="s", email="a@example.com")
        assert first.id == second.id

    async def test_unknown_subject_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(ApiError) as exc:
            await IdentityService(session).current_user("never-seen")
        assert exc.value.code == "AUTH_ACCOUNT_DISABLED"

    async def test_erased_user_stops_authenticating(self, session: AsyncSession) -> None:
        """The case that matters most.

        An erased user's token stays cryptographically valid until it expires.
        Verification alone would keep a deleted account working for the rest of
        that token's lifetime, which is why resolution is a separate step.

        Erasure deletes the profile; the anchor survives so counterparties keep
        their records (ADR-0020). Resolution joins to the profile, so it stops
        here.
        """
        from sqlalchemy import delete

        from acme.domains.identity.models import UserProfile

        service = IdentityService(session)
        user = await service.provision(auth_subject="doomed", email="doomed@example.com")

        await session.execute(delete(UserProfile).where(UserProfile.user_id == user.id))
        await session.flush()

        with pytest.raises(ApiError) as exc:
            await service.current_user("doomed")
        assert exc.value.code == "AUTH_ACCOUNT_DISABLED"
