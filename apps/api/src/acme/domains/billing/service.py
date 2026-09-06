"""Billing service.

Exposes exactly ONE thing to other domains: `EntitlementsService.check()`.
Nothing else crosses the boundary, and no code anywhere asks about a payment
provider (ADR-0009).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.domains.billing.enums import EntitlementStatus, SubjectKind
from acme.domains.billing.models import Entitlement

# The free tier, as code.
#
# A subject with no entitlement rows gets these. That matters more than it
# looks: it means a brand-new user works without anything having to write rows
# at signup, and it means a billing outage degrades to the free tier rather
# than to "entitled to nothing".
#
# Values come from 00-context/pricing.md. PRICES are never here - those live in
# App Store Connect and Stripe and are read at render time (ADR-0009).
#
# -1 means unlimited.
FREE_TIER: dict[str, int | bool] = {
    "card.limit": 1,
    "card.custom_fields": False,
    "card.qr_customisation": False,
    "card.remove_branding": False,
    "link.page_limit": 1,
    "link.custom_slug": False,
    "analytics.card": False,
    "analytics.link": False,
    # Connection HISTORY is never gated (00-context/pricing.md). Hiding
    # contacts someone already made reads as theft and generates more one-star
    # reviews than every other issue combined. Only the reminder cap is free.
    "connection.reminder_limit": 3,
    "connection.export": False,
    "connection.crm_sync": False,
    "event.attendee_limit": 50,
    "event.dashboard_live": False,
    "event.attendee_export": False,
    "event.attendee_import": False,
    "event.branded_page": False,
    "event.custom_domain": False,
    "event.announcements": False,
    "org.seat_limit": 1,
}


@dataclass(frozen=True, slots=True)
class Subject:
    """Who an entitlement belongs to: a user or an organization."""

    kind: SubjectKind
    id: UUID

    @staticmethod
    def user(user_id: UUID) -> "Subject":
        return Subject(kind=SubjectKind.USER, id=user_id)

    @staticmethod
    def organization(org_id: UUID) -> "Subject":
        return Subject(kind=SubjectKind.ORGANIZATION, id=org_id)


class EntitlementsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self, subject: Subject, key: str) -> int | bool:
        """Resolve one entitlement.

        The ONLY authorization question about a paid feature:

            if not await entitlements.check(subject, "connection.export"):
                raise ApiError("CONNECTION_EXPORT_NOT_ENTITLED")

        Never `if user.stripe_subscription_active`. Adding Google Play later
        must be one webhook handler and zero changes anywhere else.

        A user can hold entitlements from more than one source - usually by
        subscribing on the web, forgetting, and subscribing again in the app.
        Conflicts resolve deterministically: HIGHEST WINS. Both rows stay, so
        support can see the duplicate and refund one (ADR-0009).

        `entitlements_active_idx` is unique on
        (subject_kind, subject_id, entitlement_key, source) for active rows, so
        multiple rows for one key always mean multiple SOURCES. There is never
        a duplicate within a source to resolve.
        """
        if key not in FREE_TIER:
            raise KeyError(
                f"unknown entitlement key {key!r}. Register it in FREE_TIER and "
                "in 00-context/pricing.md."
            )

        stmt = select(Entitlement).where(
            Entitlement.subject_kind == subject.kind,
            Entitlement.subject_id == subject.id,
            Entitlement.entitlement_key == key,
            Entitlement.status.in_([EntitlementStatus.ACTIVE, EntitlementStatus.GRACE]),
        )
        rows = list((await self._session.execute(stmt)).scalars())
        if not rows:
            return FREE_TIER[key]

        default = FREE_TIER[key]
        if isinstance(default, bool):
            # Any granting row wins.
            return any(bool(r.value_bool) for r in rows)

        values = [r.value_int for r in rows if r.value_int is not None]
        if not values:
            return default
        # -1 is unlimited, so it beats every finite value rather than losing to
        # max(). Getting this backwards would cap a paying customer at 5.
        if -1 in values:
            return -1
        return max(values)

    async def limit(self, subject: Subject, key: str) -> int:
        value = await self.check(subject, key)
        if isinstance(value, bool):
            raise TypeError(f"{key} is a boolean entitlement, not a limit")
        return value

    async def allowed(self, subject: Subject, key: str) -> bool:
        value = await self.check(subject, key)
        if not isinstance(value, bool):
            raise TypeError(f"{key} is a numeric entitlement, not a flag")
        return value
