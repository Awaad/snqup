"""Health endpoints (contracts/api-conventions.md).

/health        liveness. Touches NOTHING. A database blip must not restart the
               process, and uptime monitoring must not page for a dependency.
/health/ready  readiness. Checks Postgres and Valkey. Gates the deploy.

The split is what lets incident triage distinguish "process is dead" from
"a dependency is dead" with two curl calls (runbooks/incident-response.md).
"""

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    checks: dict[str, str] = {}

    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        await request.app.state.redis.ping()
        checks["cache"] = "ok"
    except Exception as exc:
        checks["cache"] = f"error: {type(exc).__name__}"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if healthy else "degraded", "checks": checks}
