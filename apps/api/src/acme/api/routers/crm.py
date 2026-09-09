"""CRM connection endpoints.

WHAT STILL BLOCKS A REAL SYNC: OAuth client credentials, which need a Google
Cloud project and a HubSpot app, both of which need the product name. Every
endpoint here works today except the final `push_contact`, which raises with a
message naming what is missing rather than pretending to succeed.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel

from acme.api.deps import CurrentUserDep, JobQueueDep, SessionDep
from acme.core.errors import ApiError
from acme.domains.crm.enums import CrmProvider
from acme.domains.crm.oauth import (
    OAuthStateStore,
    TokenCipher,
    authorize_url,
    exchange_code,
)
from acme.domains.crm.schemas import (
    CrmConnectionCreate,
    CrmConnectionOut,
    FieldMappingUpdate,
    SyncRequest,
)
from acme.domains.crm.service import CrmService

router = APIRouter(prefix="/v1/crm", tags=["crm"])


class AuthorizeUrlOut(BaseModel):
    """Where to send the user, and the state bound to this request.

    The state is returned so a client can verify the callback belongs to the
    flow it started, but the SERVER verifies it independently — a client-side
    check protects nobody, because the attack is a forged callback.
    """

    authorize_url: str
    state: str


def _credentials_for(provider: CrmProvider, settings: object) -> tuple[str, str]:
    if provider == CrmProvider.GOOGLE_CONTACTS:
        return (
            settings.google_client_id,  # type: ignore[attr-defined]
            settings.google_client_secret,  # type: ignore[attr-defined]
        )
    return (
        settings.hubspot_client_id,  # type: ignore[attr-defined]
        settings.hubspot_client_secret,  # type: ignore[attr-defined]
    )


@router.get("/connections", response_model=list[CrmConnectionOut])
async def list_connections(session: SessionDep, user: CurrentUserDep) -> list[CrmConnectionOut]:
    """Connected CRMs.

    `needs_reauth` and `last_error` are the point of this response: a CRM that
    silently stopped syncing is worse than one never connected, because the
    user believes their contacts are safe.
    """
    connections = await CrmService(session, user.id).connections()
    return [CrmConnectionOut.model_validate(c) for c in connections]


@router.get("/authorize", response_model=AuthorizeUrlOut)
async def start_authorization(
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    provider: CrmProvider,
    redirect_uri: Annotated[str, Query(min_length=1)],
) -> AuthorizeUrlOut:
    """Begin the handshake.

    The state is generated and stored SERVER-SIDE, bound to this user, single
    use, ten minute TTL. Without that, an attacker can complete a flow with
    their own CRM and hand the callback to a victim, whose contacts then flow
    to the attacker while their app says "connected".
    """
    settings = request.app.state.settings
    client_id, _ = _credentials_for(provider, settings)
    if not client_id:
        raise ApiError(
            "CRM_PROVIDER_UNAVAILABLE",
            status_code=503,
            message=f"{provider.value} OAuth is not configured on this deployment",
        )

    state = await OAuthStateStore(request.app.state.redis).issue(
        str(user.id), provider, redirect_uri
    )
    return AuthorizeUrlOut(
        authorize_url=authorize_url(
            provider,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
        ),
        state=state,
    )


@router.post("/connections", response_model=CrmConnectionOut, status_code=status.HTTP_201_CREATED)
async def complete_authorization(
    payload: CrmConnectionCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
) -> CrmConnectionOut:
    """Exchange the authorization code and store the connection.

    The exchange happens SERVER-SIDE: a refresh token is a long-lived write
    credential into someone's customer database, and one sitting in a mobile
    app or in localStorage is a leak waiting for a decompiler.
    """
    settings = request.app.state.settings

    # Verified and burned before anything else. Fails closed - proceeding
    # without it would connect this account to an unverified CRM.
    await OAuthStateStore(request.app.state.redis).consume(payload.state, str(user.id))

    client_id, client_secret = _credentials_for(payload.provider, settings)
    tokens = await exchange_code(
        payload.provider,
        code=payload.code,
        redirect_uri=payload.redirect_uri,
        client_id=client_id,
        client_secret=client_secret,
    )

    connection = await CrmService(session, user.id).connect(
        payload.provider, tokens, TokenCipher(settings.crm_token_key)
    )
    return CrmConnectionOut.model_validate(connection)


@router.put("/connections/{provider}/mapping", response_model=CrmConnectionOut)
async def set_field_mapping(
    provider: CrmProvider,
    payload: FieldMappingUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> CrmConnectionOut:
    """Which CRM property receives which of our fields.

    Per CONNECTION, not per provider: two HubSpot portals name their custom
    properties differently, and hardcoding one writes into the wrong field — or
    into nothing, which is worse because it looks like it worked.
    """
    connection = await CrmService(session, user.id).set_field_mapping(provider, payload.mapping)
    return CrmConnectionOut.model_validate(connection)


@router.post("/connections/{provider}/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_now(
    provider: CrmProvider,
    payload: SyncRequest,
    session: SessionDep,
    user: CurrentUserDep,
    jobs: JobQueueDep,
) -> dict[str, str]:
    """Queue a sync.

    ACCEPTED, not OK: pushing contacts to a third party puts their latency and
    their outage inside our request. Deduplicated per connection, so a user
    hammering "sync now" gets one run rather than overlapping pushes competing
    to write the same contacts.
    """
    service = CrmService(session, user.id)
    connection = await service.get(provider)
    if connection is None:
        raise ApiError("CRM_NOT_CONNECTED", status_code=404)
    if connection.needs_reauth:
        raise ApiError(
            "CRM_REAUTH_REQUIRED",
            status_code=409,
            message="reconnect this CRM before syncing",
        )

    job_id = await jobs.sync_crm_contacts(connection.id, payload.connection_view_ids or [])
    return {"status": "queued", "job_id": job_id}


@router.delete("/connections/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(provider: CrmProvider, session: SessionDep, user: CurrentUserDep) -> None:
    """Disconnect.

    Soft delete so the partial unique index frees the slot. Synced-contact
    records go with it, or a later reconnect would believe everything had
    already been pushed and sync nothing.
    """
    await CrmService(session, user.id).disconnect(provider)
