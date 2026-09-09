"""Domain verification endpoints.

"""

from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.identity.service import IdentityService

router = APIRouter(prefix="/v1", tags=["identity"])


class DomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Pasted URLs are normalised. People paste `https://www.example.com/`, and
    #: treating that as a domain produces a lookup that can never succeed.
    domain: str = Field(min_length=3, max_length=253)


class DomainChallengeOut(BaseModel):
    domain: str
    host: str
    record_type: str
    value: str
    instructions: str
    verified: bool
    last_error: str | None = None


@router.post(
    "/organizations/{organization_id}/domains",
    response_model=DomainChallengeOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_verification(
    organization_id: UUID,
    payload: DomainRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> DomainChallengeOut:
    """Get the TXT record to publish.

    Reissues the SAME token if a challenge already exists: a user who lost the
    instructions should not end up with two pending records to publish.
    """
    verification, instructions = await IdentityService(session).start_domain_verification(
        organization_id, payload.domain
    )
    return DomainChallengeOut(
        domain=verification.domain,
        host=instructions.host,
        record_type=instructions.record_type,
        value=instructions.value,
        instructions=instructions.human,
        verified=verification.verified_at is not None,
        last_error=verification.last_error,
    )


@router.post(
    "/organizations/{organization_id}/domains/verify",
    response_model=DomainChallengeOut,
)
async def check_verification(
    organization_id: UUID,
    payload: DomainRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> DomainChallengeOut:
    """Check the record now.

    A failure is NOT an error state. DNS propagation is slow and the normal
    experience is checking too early, so this records the attempt and the user
    tries again — `verified: false` with `last_error` is the expected first
    response, not a problem.
    """
    from acme.domains.identity.domains import instructions_for

    service = IdentityService(session)
    verification = await service.check_domain_verification(organization_id, payload.domain)
    instructions = instructions_for(verification.domain, verification.challenge_token)
    return DomainChallengeOut(
        domain=verification.domain,
        host=instructions.host,
        record_type=instructions.record_type,
        value=instructions.value,
        instructions=instructions.human,
        verified=verification.verified_at is not None,
        last_error=verification.last_error,
    )
