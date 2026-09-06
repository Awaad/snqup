"""Application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from acme.api.health import router as health_router
from acme.api.middleware import RequestContextMiddleware
from acme.core.auth import JwtVerifier
from acme.core.cache import create_redis
from acme.core.config import get_settings
from acme.core.db import create_engine, create_listen_engine, create_session_factory
from acme.core.errors import ApiError, api_error_handler
from acme.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    app.state.verifier = JwtVerifier(
        public_keys=settings.jwt_public_keys,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )

    yield

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
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]

    app.include_router(health_router)
    # Domain routers mount here as they land:
    #   app.include_router(cards_router, prefix="/v1")

    return app


app = create_app()
