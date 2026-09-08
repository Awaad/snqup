"""FastAPI dependencies.

The composition point between HTTP and the domains. Routers depend on these and
never construct a session, a verifier or a tenant themselves.

Public endpoints are listed explicitly in `api/public_routes.py`. The default is
authenticated, and opting out is deliberate and visible - an endpoint that is
public because someone forgot a decorator is the failure mode this avoids
(contracts/api-conventions.md).
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.cache import RateLimiter
from acme.core.errors import ApiError
from acme.core.jobs import JobQueue
from acme.core.repository import Tenant
from acme.domains.identity.service import CurrentUser, IdentityService

# auto_error=False so a missing header produces our error envelope with a
# registered code, not FastAPI's default JSON shape. Clients switch on `code`.
_bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """One session per request, committed on success, rolled back on error.

    Committing here rather than in each service is what makes a multi-domain
    write atomic: the exchange transaction spans four domains and must be one
    commit or none.
    """
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


RedisDep = Annotated[Redis, Depends(get_redis)]


def get_rate_limiter(redis: RedisDep) -> RateLimiter:
    return RateLimiter(redis)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_job_queue(request: Request) -> JobQueue:
    return JobQueue(request.app.state.job_pool)


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


# Per-authenticated-user limits. A human scans perhaps thirty cards in a day,
# not three hundred; a script does the opposite (`handoff/03-backend-api.md`).
#
# Applied to WRITES only. Reading your own connection list quickly is normal
# behaviour at an event, and throttling it would punish the engaged user.
USER_WRITE_LIMIT = 300
USER_WRITE_WINDOW_SECONDS = 3600


async def enforce_user_write_limit(user: "CurrentUserDep", limiter: RateLimiterDep) -> None:
    """Ceiling on authenticated writes.

    FAILS OPEN with logging, like every other limit here (ADR-0007). A false
    positive during a live event, in front of four hundred people, is worse
    than an unthrottled hour.
    """
    await limiter.check(
        f"user:write:{user.id}",
        limit=USER_WRITE_LIMIT,
        window_seconds=USER_WRITE_WINDOW_SECONDS,
    )


UserWriteLimit = Depends(enforce_user_write_limit)


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Verify the JWT and resolve it to our user.

    Two steps that must stay separate: the verifier says the token is
    authentic, and the identity service says the account still exists. An
    erased user's token stays cryptographically valid until it expires, so
    skipping the second step would keep a deleted account working for the
    remainder of its token lifetime.
    """
    if credentials is None:
        raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="missing bearer token")

    claims = await request.app.state.verifier.verify(credentials.credentials)

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ApiError("AUTH_TOKEN_INVALID", status_code=401, message="token has no subject")

    return await IdentityService(session).current_user(subject)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_user_tenant(user: CurrentUserDep) -> Tenant:
    """The caller acting on their own data.

    Organization tenancy is NOT derived here. Acting for an organization
    requires proving membership, which is a service call with its own failure
    modes (ORG_ROLE_INSUFFICIENT), not something a dependency can infer from a
    token. Endpoints that need it take the organization id as a path parameter
    and resolve it explicitly.
    """
    return Tenant.user(user.id)


UserTenantDep = Annotated[Tenant, Depends(get_user_tenant)]
