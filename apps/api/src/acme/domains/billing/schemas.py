"""Billing request and response schemas.

NO PRICES. They live in App Store Connect and Stripe and are read at render
time; one here would drift from the store within a release and nobody would
notice until a customer did (ADR-0009).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from acme.domains.billing.enums import EntitlementSource, EntitlementStatus


class EntitlementsOut(BaseModel):
    """The caller's resolved capabilities.

    A flat map, which is what the client actually needs: it never asks "do they
    have a Stripe subscription", only "can they export". Adding Google Play
    later changes nothing here.
    """

    entitlements: dict[str, int | bool]


class SubscriptionOut(BaseModel):
    """Shown in account settings so a user can see what they are paying for.

    `source` matters to them: someone who subscribed through Apple must cancel
    through Apple, and telling them otherwise sends them to a page that cannot
    help.
    """

    model_config = ConfigDict(from_attributes=True)

    plan_key: str
    source: EntitlementSource
    status: EntitlementStatus
    current_period_end: datetime | None


class WebhookAck(BaseModel):
    """Providers retry on non-2xx.

    A duplicate is expected and harmless - handlers are idempotent against
    billing_events - so a repeat is acknowledged rather than errored.
    """

    received: bool = True
    duplicate: bool = False
