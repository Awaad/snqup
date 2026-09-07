"""Privacy endpoints: export and account deletion.

Both are legal obligations rather than features, and both must be reachable
in-app without contacting support - Apple guideline 5.1.1(v) for deletion,
GDPR Articles 17 and 20 for the rest.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.identity.service import IdentityService
from acme.domains.safety.export import DataExportService

router = APIRouter(prefix="/v1", tags=["privacy"])

# The grace period turns accidental deletion from a catastrophe into a support
# conversation. The entire value of the app is the user's contacts.
DELETION_GRACE_DAYS = 30


@router.get("/privacy/export")
async def export_my_data(session: SessionDep, user: CurrentUserDep) -> JSONResponse:
    """GDPR Article 20. ALWAYS FREE.

    CONNECTION_EXPORT_NOT_ENTITLED must never be returned here. The paid
    workflow export is a different endpoint with a different name in the UI,
    because gating a legal right behind a subscription is how a support ticket
    becomes a regulatory one (ADR-0020).
    """
    payload = await DataExportService(session).build(user.id)
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": 'attachment; filename="my-data.json"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/privacy/account", status_code=status.HTTP_202_ACCEPTED)
async def delete_my_account(session: SessionDep, user: CurrentUserDep) -> dict[str, str]:
    """Delete this account. In-app, no support contact, no dark patterns.

    Immediate: the account is disabled, tokens are revoked, public pages 404.
    After the grace period the purge job hard-deletes the profile row, which is
    what makes erasure real - soft delete alone is a claim we would not be
    keeping.

    The `users` anchor survives so counterparties keep their record of a
    meeting that happened. That position is disclosed in the privacy policy.
    """
    purge_after = datetime.now(UTC) + timedelta(days=DELETION_GRACE_DAYS)
    await IdentityService(session).schedule_deletion(user.id, purge_after)
    return {
        "status": "scheduled",
        "purge_after": purge_after.isoformat(),
        "grace_days": str(DELETION_GRACE_DAYS),
    }
