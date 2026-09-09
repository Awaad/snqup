"""OAuth for CRM providers.

The code here is complete. What is missing is CREDENTIALS - a Google Cloud
project and a HubSpot app, neither of which can be registered until the product
has a name. They come from configuration, so nothing here changes when they
exist.

TOKEN STORAGE. A CRM refresh token is a write credential into someone's
customer database: the blast radius of a leak is their business, not their
account here. Encrypted at rest with a key that is not the database password,
so a database dump alone does not yield working credentials.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken

from acme.core.errors import ApiError
from acme.domains.crm.adapter import CrmAuthError
from acme.domains.crm.enums import CrmProvider

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/contacts",)

HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"  # noqa: S105
HUBSPOT_SCOPES = ("crm.objects.contacts.write", "crm.objects.contacts.read")

#: Refresh this far before actual expiry. A token that expires mid-request
#: fails a sync the user triggered and looks like a broken integration.
REFRESH_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...] = ()

    @property
    def needs_refresh(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at - REFRESH_SKEW


class TokenCipher:
    """Encrypt tokens at rest.

    Fernet: authenticated, so a tampered ciphertext fails rather than
    decrypting to garbage that gets sent to a provider as a credential.

    The key is separate from the database password on purpose. If they were the
    same, anyone who could read the database could also use what they read.
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError(
                "CRM_TOKEN_KEY is not set. Refusing to store CRM credentials "
                "in plaintext - they are write access to someone's customer "
                "database."
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, tokens: OAuthTokens) -> bytes:
        payload = {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            "scopes": list(tokens.scopes),
        }
        return self._fernet.encrypt(json.dumps(payload).encode())

    def decrypt(self, blob: bytes) -> OAuthTokens:
        try:
            payload = json.loads(self._fernet.decrypt(blob))
        except (InvalidToken, ValueError) as exc:
            # Key rotated without re-encrypting, or the row is corrupt. Either
            # way the user must reconnect - silently retrying would spin
            # forever against credentials that can never work.
            raise CrmAuthError("stored credentials are unreadable") from exc
        expires = payload.get("expires_at")
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=datetime.fromisoformat(expires) if expires else None,
            scopes=tuple(payload.get("scopes", [])),
        )


def _expires_at(response: dict[str, object]) -> datetime | None:
    seconds = response.get("expires_in")
    if not isinstance(seconds, int | float):
        return None
    return datetime.now(UTC) + timedelta(seconds=float(seconds))


async def exchange_code(
    provider: CrmProvider,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> OAuthTokens:
    """Swap an authorization code for tokens.

    Done SERVER-SIDE. The client never holds a refresh token, because a
    long-lived credential to someone's CRM sitting in a mobile app or in
    localStorage is a leak waiting for a decompiler.
    """
    # == not `is` throughout this module. CrmProvider is a StrEnum and a raw
    # string reaching here - from a database row read before refresh, or from a
    # caller passing the literal - makes identity comparison silently take the
    # WRONG branch, posting a Google authorization code to HubSpot.
    url = GOOGLE_TOKEN_URL if provider == CrmProvider.GOOGLE_CONTACTS else HUBSPOT_TOKEN_URL
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, data=form)

    if response.status_code >= 400:
        raise ApiError(
            "CRM_OAUTH_FAILED",
            status_code=400,
            message="provider rejected the authorization code",
            details={"provider": provider.value, "status": response.status_code},
        )

    payload = response.json()
    refresh = payload.get("refresh_token")
    if not refresh and provider == CrmProvider.GOOGLE_CONTACTS:
        # Google only returns a refresh token when access_type=offline and the
        # user has not already granted consent. Without one the connection dies
        # in an hour and the user has no idea why, so fail here where the cause
        # is still visible.
        raise ApiError(
            "CRM_OAUTH_FAILED",
            status_code=400,
            message=("no refresh token returned; request access_type=offline and prompt=consent"),
        )

    return OAuthTokens(
        access_token=payload["access_token"],
        refresh_token=refresh,
        expires_at=_expires_at(payload),
        scopes=tuple(str(payload.get("scope", "")).split()),
    )


async def refresh_tokens(
    provider: CrmProvider,
    tokens: OAuthTokens,
    *,
    client_id: str,
    client_secret: str,
) -> OAuthTokens:
    """Refresh an access token.

    A failure here means the user revoked access or the grant expired, so it
    raises CrmAuthError rather than a transient error - retrying a revoked
    grant forever produces a queue that never drains and a user who is never
    told their sync stopped.
    """
    if not tokens.refresh_token:
        raise CrmAuthError("no refresh token stored")

    url = GOOGLE_TOKEN_URL if provider == CrmProvider.GOOGLE_CONTACTS else HUBSPOT_TOKEN_URL
    form = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, data=form)

    if response.status_code >= 400:
        raise CrmAuthError(f"{provider.value} refused the refresh token")

    payload = response.json()
    return OAuthTokens(
        access_token=payload["access_token"],
        # Google omits refresh_token on refresh; keeping the old one is what
        # stops a refresh from silently unlinking the account.
        refresh_token=payload.get("refresh_token") or tokens.refresh_token,
        expires_at=_expires_at(payload),
        scopes=tokens.scopes,
    )


def authorize_url(provider: CrmProvider, *, client_id: str, redirect_uri: str, state: str) -> str:
    """Where to send the user to grant access.

    `state` is a CSRF token bound to the session, not decoration: without it,
    an attacker can complete an OAuth flow that connects THEIR CRM to the
    victim's account, and every contact the victim collects is then pushed to
    the attacker.
    """
    from urllib.parse import urlencode

    if provider == CrmProvider.GOOGLE_CONTACTS:
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            # Both required for a refresh token. Without them the connection
            # silently dies an hour after it is made.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(HUBSPOT_SCOPES),
        "state": state,
    }
    return f"https://app.hubspot.com/oauth/authorize?{urlencode(params)}"


def generate_state() -> str:
    import secrets

    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")


class OAuthStateStore:
    """CSRF state for the OAuth handshake.

    THE ATTACK this prevents, which is easy to underrate: without a bound,
    verified state, an attacker completes an authorization flow with THEIR CRM
    account and delivers the resulting callback URL to a victim. The victim's
    account is then connected to the attacker's CRM, and every contact the
    victim collects is pushed to them.

    Nothing about that looks wrong to the victim - their sync says "connected".

    Three properties, all necessary:

      bound        stored against the user who STARTED the flow, so a callback
                   cannot be replayed into a different account
      single use   consumed on verification, so a leaked callback URL cannot be
                   replayed
      short lived  ten minutes is longer than any real handshake and shorter
                   than a stolen link is useful
    """

    TTL_SECONDS = 600

    def __init__(self, redis: object) -> None:
        self._redis = redis

    @staticmethod
    def _key(state: str) -> str:
        return f"crmstate:{state}"

    async def issue(self, user_id: str, provider: CrmProvider, redirect_uri: str) -> str:
        state = generate_state()
        await self._redis.set(  # type: ignore[attr-defined]
            self._key(state),
            json.dumps(
                {
                    "user_id": user_id,
                    "provider": str(provider),
                    "redirect_uri": redirect_uri,
                }
            ),
            ex=self.TTL_SECONDS,
        )
        return state

    async def consume(self, state: str, user_id: str) -> dict[str, str]:
        """Verify and burn.

        FAILS CLOSED, unlike rate limiting and idempotency. Those degrade a
        defence when Valkey is unavailable; this one IS the defence, and
        proceeding without it would connect an account to an unverified CRM.
        """
        raw = await self._redis.get(self._key(state))  # type: ignore[attr-defined]
        if raw is None:
            raise ApiError(
                "CRM_OAUTH_STATE_INVALID",
                status_code=400,
                message="authorization state is missing, expired or already used",
            )
        await self._redis.delete(self._key(state))  # type: ignore[attr-defined]

        stored: dict[str, str] = json.loads(raw)
        if stored.get("user_id") != user_id:
            # The callback belongs to a different account. This is the attack,
            # not a mistake.
            raise ApiError(
                "CRM_OAUTH_STATE_INVALID",
                status_code=400,
                message="authorization state does not belong to this account",
            )
        return stored
