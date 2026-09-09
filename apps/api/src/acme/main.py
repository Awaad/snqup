"""Application entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from acme.api.health import router as health_router
from acme.api.idempotency_middleware import IdempotencyMiddleware
from acme.api.middleware import RequestContextMiddleware
from acme.api.routers.admin import router as admin_router
from acme.api.routers.cards import public_router as cards_public_router
from acme.api.routers.cards import router as cards_router
from acme.api.routers.connections import router as connections_router
from acme.api.routers.crm import router as crm_router
from acme.api.routers.dashboard import router as dashboard_router
from acme.api.routers.events import public_router as events_public_router
from acme.api.routers.events import router as events_router
from acme.api.routers.exchange import router as exchange_router
from acme.api.routers.me import router as me_router
from acme.api.routers.media import router as media_router
from acme.api.routers.notifications import router as notifications_router
from acme.api.routers.privacy import router as privacy_router
from acme.api.routers.scan import router as scan_router
from acme.api.routers.webhooks import router as webhooks_router
from acme.core.auth import build_verifier
from acme.core.cache import create_redis
from acme.core.config import get_settings
from acme.core.db import create_engine, create_listen_engine, create_session_factory
from acme.core.errors import ApiError, api_error_handler
from acme.core.jobs import create_job_pool
from acme.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()

    configure_logging(
        level=settings.log_level,
        service="api",
        env=settings.environment,
        version=settings.version,
    )

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            release=settings.version,
            # Session replay and PII would turn our error reports into a
            # contact database. Off, deliberately (ADR-0024).
            send_default_pii=False,
        )

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    # Separate direct connection - must NOT be the pooler (ADR-0006).
    app.state.listen_engine = create_listen_engine(settings)
    app.state.redis = create_redis(settings)
    # Separate pool for enqueueing. ARQ's client and our cache client have
    # different lifecycles, and sharing one means a cache issue takes the job
    # queue with it.
    app.state.job_pool = await create_job_pool(str(settings.redis_url))
    app.state.settings = settings
    app.state.verifier = build_verifier(settings)

    yield

    await app.state.job_pool.aclose()
    await app.state.redis.aclose()
    await app.state.listen_engine.dispose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="API",
        # Everything under /v1. Mobile clients cannot be force-updated, so we
        # will run this version for years (contracts/api-conventions.md).
        version="1",
        # No custom response class. FastAPI serializes directly to JSON bytes
        # via Pydantic when a return type or response_model is set, which is
        # faster than routing through one - and ORJSONResponse is deprecated
        # for exactly that reason. Every endpoint here declares a response
        # model, so there is nothing to gain from overriding it.
        lifespan=lifespan,
        # ALL THREE disabled in production, not just /docs.
        # There is no reason to hand an attacker a complete map
        # of the surface, and CI generates the client from
        # `acme.scripts.export_openapi` rather than from a running server, so
        # nothing depends on these being reachable.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Order matters: RequestContextMiddleware runs FIRST so a replayed
    # response still carries a request id, and so idempotency failures are
    # logged with one.
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]

    app.include_router(health_router)
    app.include_router(cards_router)
    app.include_router(cards_public_router)
    app.include_router(exchange_router)
    app.include_router(connections_router)
    app.include_router(scan_router)
    app.include_router(events_router)
    app.include_router(events_public_router)
    app.include_router(dashboard_router)
    app.include_router(privacy_router)
    app.include_router(me_router)
    app.include_router(media_router)
    app.include_router(notifications_router)
    app.include_router(crm_router)
    app.include_router(webhooks_router)
    app.include_router(admin_router)

    return app


app = create_app()
