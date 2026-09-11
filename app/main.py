"""Application entry point.

`create_app()` assembles the service from its parts; `app` is the instance
uvicorn serves (`uvicorn app.main:app`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api import meta, versions
from app.api.errors import register_exception_handlers
from app.api.middleware import RateLimitMiddleware, VersionHeadersMiddleware
from app.api.openapi import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    OPENAPI_TAGS,
    use_clean_openapi,
)
from app.api.v1.router import router as v1_router
from app.container import Services
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, services: Services | None = None) -> FastAPI:
    """Build the application.

    Pass `services` to run the app on substitutes, as the tests do; otherwise
    the real services are built from `settings` at startup.
    """
    if settings is None:
        settings = services.settings if services is not None else get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail fast. Without a key the service would boot, report healthy, and
        # then fail every request - so refuse to start instead.
        if not settings.resolved_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set.\n"
                "  Put your key in .env:  OPENAI_API_KEY=sk-...\n"
                "  or export it:          export OPENAI_API_KEY=sk-...\n"
                "  Get a key at https://platform.openai.com/api-keys"
            )
        app.state.services = services if services is not None else Services.build(settings)
        logger.info(
            "Niwas AI ready (model=%s, properties=%s, versions=%s)",
            settings.model,
            settings.properties_api_base_url,
            ", ".join(sorted(versions.VERSIONS)),
        )
        try:
            yield
        finally:
            if services is None:  # close only what this app built
                await app.state.services.aclose()

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=API_VERSION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    use_clean_openapi(app)

    # Middleware added last runs first: version headers -> CORS -> rate limit
    # -> routes. The rate limiter sits inside CORS so its 429 responses still
    # carry CORS headers a browser can read.
    app.add_middleware(RateLimitMiddleware, limit=settings.rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # The API uses no cookies or auth headers. With credentials on, "*"
        # would echo back any site's origin as allowed.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-API-Version",
            "X-API-Latest-Version",
            "Deprecation",
            "Sunset",
            "Link",
            "Retry-After",
        ],
    )
    app.add_middleware(VersionHeadersMiddleware)

    register_exception_handlers(app)

    app.include_router(meta.router)
    app.include_router(v1_router)  # adding v2 is one more include_router

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
