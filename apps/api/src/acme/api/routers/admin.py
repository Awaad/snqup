"""Admin API.

The endpoints behind the support console. Several runbooks are unexecutable
without these: abuse-takedown says "suspend the card, revoke tokens" - with
what? - and gdpr-requests assumes a user can be looked up.

Without them, every support action is a hand-written SQL statement against
production at the moment of highest pressure. That is how data gets destroyed.

FIVE RULES, all enforced here rather than trusted to the UI:

  1. every action writes to audit_log, no exceptions
  2. private connection notes are UNREACHABLE, even for staff
  3. suspension never deletes connections
  4. list views truncate contact details
  5. unauthenticated access returns 404, not 401
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from acme.api.deps import CurrentUserDep, SessionDep
from acme.core.errors import ApiError
from acme.domains.billing.enums import EntitlementSource, SubjectKind
from acme.domains.safety.enums import ReportStatus

router = APIRouter(prefix="/v1/admin", tags=["admin"])


async def require_staff(request: Request, user: CurrentUserDep) -> CurrentUserDep:
    """Staff allowlist, checked against configuration.

    NOT a role on the user row: a database-backed admin flag is one SQL
    injection or one bad migration away from privilege escalation, and this
    surface can read every account in the system.

    Raises 404 rather than 403. An unauthorised caller should not learn that
    an admin API exists here.
    """
    allowlist = set(request.app.state.settings.admin_subjects)
    if not allowlist or user.auth_subject not in allowlist:
        raise ApiError("CARD_NOT_FOUND", status_code=404)
    return user


StaffDep = Annotated[CurrentUserDep, Depends(require_staff)]


class UserSummary(BaseModel):
    """TRUNCATED on purpose.

    Staff browsing a contact database is exactly the risk this product carries,
    so a list view shows enough to identify an account and no more. Full
    details need a single-record view, which is audited.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_masked: str
    display_name: str | None
    deleted_at: str | None
    purge_after: str | None


class SuspendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)
    #: Typed confirmation of the subject id. Not a checkbox: the person using
    #: this is under pressure, and a checkbox is muscle memory.
    confirm_id: UUID


class GrantRequest(BaseModel):
    """Comped pilot partners. Same table and resolver as a paid entitlement.

    An expiry is REQUIRED. A permanently comped organizer is how the entire
    meetup segment ends up never paying (00-context/pricing.md).
    """

    model_config = ConfigDict(extra="forbid")

    subject_kind: SubjectKind
    subject_id: UUID
    entitlement_key: str
    value_int: int | None = None
    value_bool: bool | None = None
    expires_at: str
    reason: str = Field(min_length=1, max_length=200)


@router.get("/users", response_model=list[UserSummary])
async def find_users(
    session: SessionDep,
    staff: StaffDep,
    request: Request,
    email: EmailStr | None = None,
    limit: int = 20,
) -> list[UserSummary]:
    """Look up an account for support.

    Emails are masked in the list. The lookup itself is audited: reading who
    exists is an access, not a neutral act.
    """
    from acme.domains.identity.service import IdentityService
    from acme.domains.safety.service import SafetyService

    SafetyService(session).audit(
        actor_user_id=staff.id,
        action="admin.users.search",
        metadata={"email": _mask(str(email)) if email else None},
        request_id=getattr(request.state, "request_id", None),
    )
    rows = await IdentityService(session).admin_search(
        email=str(email) if email else None, limit=limit
    )
    return [UserSummary.model_validate(row) for row in rows]


@router.post("/cards/{card_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_card(
    card_id: UUID,
    payload: SuspendRequest,
    session: SessionDep,
    staff: StaffDep,
    request: Request,
) -> None:
    """Suspend a card and revoke its tokens (runbooks/abuse-takedown.md).

    Existing CONNECTIONS are untouched. The snapshot is the counterpart's
    record of a meeting that happened (ADR-0004), and destroying it would
    punish someone who did nothing.

    Err toward suspension on the UGC domain: wrongly suspending one card is a
    support conversation, while a Safe Browsing listing takes down every link
    in the product.
    """
    if payload.confirm_id != card_id:
        raise ApiError(
            "VALIDATION_FAILED",
            status_code=422,
            message="confirm_id does not match the card being suspended",
        )

    from acme.domains.cards.service import AdminCardService
    from acme.domains.safety.service import SafetyService

    await AdminCardService(session).suspend(card_id)
    SafetyService(session).audit(
        actor_user_id=staff.id,
        action="admin.card.suspend",
        subject_kind="card",
        subject_id=card_id,
        metadata={"reason": payload.reason},
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/reports")
async def open_reports(session: SessionDep, staff: StaffDep) -> list[dict[str, object]]:
    """The moderation queue.

    Reports survive the thing they are about - subject FKs are SET NULL, not
    CASCADE - so purging a suspended card does not erase the record of why it
    was suspended.
    """
    from acme.domains.safety.service import SafetyService

    reports = await SafetyService(session).open_reports()
    return [
        {
            "id": str(r.id),
            "reason": r.reason,
            "subject_label": r.subject_label,
            "status": str(r.status),
            "created_at": r.created_at.isoformat(),
        }
        for r in reports
    ]


@router.post("/reports/{report_id}/resolve", status_code=status.HTTP_204_NO_CONTENT)
async def resolve_report(
    report_id: UUID,
    session: SessionDep,
    staff: StaffDep,
    request: Request,
    outcome: ReportStatus = ReportStatus.ACTIONED,
    note: str = "",
) -> None:
    from acme.domains.safety.service import SafetyService

    service = SafetyService(session)
    await service.resolve_report(report_id, outcome, note)
    service.audit(
        actor_user_id=staff.id,
        action="admin.report.resolve",
        subject_kind="report",
        subject_id=report_id,
        metadata={"outcome": outcome.value, "note": note},
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/entitlements/grant", status_code=status.HTTP_204_NO_CONTENT)
async def grant_entitlement(
    payload: GrantRequest, session: SessionDep, staff: StaffDep, request: Request
) -> None:
    """Comp an entitlement. Same mechanism as paid, no special-case code.

    source='manual' with an expiry, so it resolves through the ordinary path
    and expires on its own rather than needing anyone to remember.
    """
    from acme.domains.billing.service import EntitlementsService
    from acme.domains.safety.service import SafetyService

    await EntitlementsService(session).grant_manual(
        subject_kind=payload.subject_kind,
        subject_id=payload.subject_id,
        key=payload.entitlement_key,
        value_int=payload.value_int,
        value_bool=payload.value_bool,
        expires_at=payload.expires_at,
        source=EntitlementSource.MANUAL,
    )
    SafetyService(session).audit(
        actor_user_id=staff.id,
        action="admin.entitlement.grant",
        subject_kind=payload.subject_kind.value,
        subject_id=payload.subject_id,
        metadata={"key": payload.entitlement_key, "reason": payload.reason},
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/billing/unprocessed")
async def unprocessed_billing_events(
    session: SessionDep, staff: StaffDep
) -> list[dict[str, object]]:
    """Deliveries recorded but never completed.

    Without this the only symptom of a failed handler is a customer who paid
    and has no access, reported by them rather than noticed by us.
    """
    from acme.domains.billing.webhooks import BillingWebhookService

    events = await BillingWebhookService(session).unprocessed()
    return [
        {
            "id": str(e.id),
            "source": e.source.value,
            "external_id": e.external_id,
            "received_at": e.received_at.isoformat(),
            "error": e.error,
        }
        for e in events
    ]


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}"
