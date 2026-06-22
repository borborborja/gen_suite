"""FastAPI application factory.

Mounts functional module routers under /api and lets the connector registry mount any
server-enabled connectors. Long-running work (transcription, downloads) is dispatched to
the ARQ worker, never run inline here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .connectors.registry import discover
from .core import events, storage
from .core.errors import AppError, app_error_handler
from .modules.api_keys.router import router as api_keys_router
from .modules.auth.router import router as auth_router
from .modules.documents.router import router as documents_router
from .modules.extraction.router import router as extraction_router
from .modules.geo.router import router as geo_router
from .modules.sources.router import router as sources_router
from .modules.jobs.router import router as jobs_router
from .modules.linkage.router import router as linkage_router
from .modules.media.router import router as media_router
from .modules.providers.router import router as providers_router
from .modules.search.router import router as search_router
from .modules.tenancy.router import router as tenancy_router
from .modules.transcription.router import router as transcription_router
from .modules.tree.router import router as tree_router
from .settings import settings

# Each functional module exposes an APIRouter named `router`, mounted under /api.
MODULE_ROUTERS = [
    auth_router,
    tenancy_router,
    tree_router,
    documents_router,
    jobs_router,
    providers_router,
    transcription_router,
    search_router,
    extraction_router,
    linkage_router,
    geo_router,
    sources_router,
    media_router,
    api_keys_router,
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await storage.ensure_buckets()
    except Exception:  # MinIO may not be ready yet; buckets are also creatable on demand
        pass
    yield
    await events.get_redis().aclose()


def create_app() -> FastAPI:
    # Fail fast in non-dev if secrets are still the publicly-known placeholders.
    problems = settings.validate_runtime_secrets()
    if problems:
        raise RuntimeError(
            "Insecure configuration for environment=" + settings.environment + ":\n  - "
            + "\n  - ".join(problems)
            + "\nSet strong values in .env (or ENVIRONMENT=development to bypass for local work)."
        )

    app = FastAPI(title="gen_suite API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Bearer-token auth (no cookies) → credentials not needed; keeping it False avoids the
        # cross-origin-cookie footgun if auth ever moves to cookies.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "environment": settings.environment}

    for router in MODULE_ROUTERS:
        app.include_router(router, prefix="/api")

    connectors = discover()
    connectors.mount(app, settings)

    @app.get("/api/connectors", tags=["meta"])
    async def list_connectors() -> dict:
        return {"enabled": connectors.capabilities(settings)}

    return app


app = create_app()
