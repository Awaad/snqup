"""The exchange endpoint.

One endpoint, and the most important one in the product.
"""

from typing import Annotated

from fastapi import APIRouter, Header, status

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.exchange.schemas import ExchangeRequest, ExchangeResult
from acme.domains.exchange.service import ExchangeService

router = APIRouter(prefix="/v1", tags=["exchange"])


@router.post("/exchanges", response_model=ExchangeResult, status_code=status.HTTP_201_CREATED)
async def create_exchange(
    payload: ExchangeRequest,
    session: SessionDep,
    user: CurrentUserDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExchangeResult:
    """Record an exchange.

    Idempotent at two levels, and both are needed. `Idempotency-Key` is handled
    by middleware and short-circuits an identical retry before this runs; the
    unique index on (pair, event) catches the same meeting submitted twice with
    DIFFERENT keys, which is two devices syncing the same offline queue.

    Neither alone is enough: the header cannot span devices, and the index
    cannot stop a duplicate card creation.

    Offline retry makes duplicates the NORMAL case, not an edge case
    (ADR-0016), so a repeat returns the original result rather than an error.

    Symmetric or one-way is decided by the TOKEN TYPE, never by the request
    (ADR-0002).
    """
    return await ExchangeService(session, user.id).exchange(payload)
