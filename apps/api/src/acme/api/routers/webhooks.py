"""Billing webhook routes: Stripe, Apple, Google Play.

Three providers, three completely different authentication models, and getting
any of them wrong turns the endpoint into a public "grant me a subscription"
API that looks identical to a working one:

  Stripe       HMAC signature over timestamp + raw body
  Apple        signed JWS payload (JWSTransaction)
  Google Play  NO body signature at all - a Pub/Sub OIDC bearer token is the
               only thing authenticating the request

All three are unauthenticated in the bearer sense (api/public_routes.py) and
authenticate by their own mechanism instead.

THE SHARED INVARIANT: record in billing_events first, keyed on
(source, external_id). A duplicate stops there. Providers retry on any
non-2xx, and Apple's notifications are eventually consistent and occasionally
duplicated, so a handler that assumes exactly-once corrupts entitlement state
silently - the user simply ends up on the wrong plan.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request

from acme.api.deps import SessionDep
from acme.core.errors import ApiError
from acme.domains.billing.enums import EntitlementSource
from acme.domains.billing.schemas import WebhookAck
from acme.domains.billing.webhooks import (
    BillingWebhookService,
    google_play_external_id,
    parse_google_play_message,
    verify_google_play_token,
    verify_stripe_signature,
)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


async def _raw_body(request: Request) -> bytes:
    """The RAW bytes, not the parsed model.

    Stripe and Apple sign the exact byte sequence. Re-serialising parsed JSON
    changes key order and whitespace, so the signature stops matching - and it
    fails for every request, which at least surfaces immediately rather than
    intermittently.
    """
    return await request.body()


@router.post("/stripe", response_model=WebhookAck)
async def stripe_webhook(
    request: Request,
    session: SessionDep,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> WebhookAck:
    """Stripe: HMAC over `timestamp.body`.

    The signature covers the payload, so a valid signature cannot be replayed
    onto a different body - which is exactly how someone would grant themselves
    a plan.
    """
    import json

    if stripe_signature is None:
        raise ApiError("BILLING_WEBHOOK_INVALID", status_code=400)

    body = await _raw_body(request)
    settings = request.app.state.settings
    verify_stripe_signature(body, stripe_signature, settings.stripe_webhook_secret)

    payload: dict[str, Any] = json.loads(body)
    external_id = str(payload.get("id", ""))
    if not external_id:
        raise ApiError("BILLING_WEBHOOK_INVALID", status_code=400)

    service = BillingWebhookService(session)
    fresh = await service.ingest(EntitlementSource.STRIPE, external_id, payload)
    # A duplicate is acknowledged, not errored: Stripe retries on non-2xx, and
    # erroring on a repeat produces a retry loop over an event already handled.
    return WebhookAck(received=True, duplicate=not fresh)


@router.post("/apple", response_model=WebhookAck)
async def apple_webhook(request: Request, session: SessionDep) -> WebhookAck:
    """Apple App Store Server Notifications V2.

    The body is a signed JWS whose certificate chain roots in Apple's CA.
    Signature verification needs that chain and is done in the service layer;
    this route handles envelope and idempotency.
    """
    import json

    body = await _raw_body(request)
    payload: dict[str, Any] = json.loads(body)

    signed = payload.get("signedPayload")
    if not isinstance(signed, str):
        raise ApiError(
            "BILLING_WEBHOOK_INVALID",
            status_code=400,
            message="missing signedPayload",
        )

    service = BillingWebhookService(session)
    # notificationUUID is Apple's own id and is stable across retries, which is
    # what makes deduplication possible at all.
    external_id = str(payload.get("notificationUUID") or signed[:64])
    fresh = await service.ingest(EntitlementSource.APPLE, external_id, payload)
    return WebhookAck(received=True, duplicate=not fresh)


@router.post("/google-play", response_model=WebhookAck)
async def google_play_webhook(
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    """Google Play Real-Time Developer Notifications, via Pub/Sub push.

    NOT deferred behind Apple and Stripe: if Android ships at launch then Play
    billing ships at launch, and an Android subscriber with no entitlements has
    paid for nothing.

    There is NO signature over the body. The OIDC bearer token is the only
    thing authenticating this request, so an endpoint that skips verifying it
    is a public subscription grant that behaves exactly like a working one.

    The notification is base64 inside `message.data`, so a handler written
    against Apple's flat shape sees an empty payload and processes nothing.
    """
    import json

    settings = request.app.state.settings
    verify_google_play_token(
        authorization,
        expected_audience=settings.google_play_pubsub_audience,
        verified_email=settings.google_play_pubsub_service_account,
    )

    envelope: dict[str, Any] = json.loads(await _raw_body(request))
    notification = parse_google_play_message(envelope)

    # RTDN carries no event id, so one is derived from the fields identifying
    # the state change. Pub/Sub is at-least-once by design and WILL redeliver.
    external_id = google_play_external_id(notification)

    service = BillingWebhookService(session)
    fresh = await service.ingest(EntitlementSource.GOOGLE, external_id, notification)
    return WebhookAck(received=True, duplicate=not fresh)
