"""FastAPI application factory.

Mounts functional module routers under /api and lets the connector registry mount any
server-enabled connectors. Long-running work (transcription, downloads) is dispatched to
the ARQ worker, never run inline here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .connectors.registry import discover
from .core import events, storage
from .core.errors import AppError, app_error_handler
from .modules.api_keys.router import router as api_keys_router
from .modules.auth.router import router as auth_router
from .modules.collaboration.router import router as collaboration_router
from .modules.documents.router import router as documents_router
from .modules.extraction.router import router as extraction_router
from .modules.geo.router import router as geo_router
from .modules.interop.router import router as interop_router
from .modules.sources.router import router as sources_router
from .modules.storage_audit.router import router as storage_audit_router
from .modules.storage_audit.quota_router import router as storage_quota_router
from .modules.jobs.router import router as jobs_router
from .modules.linkage.router import router as linkage_router
from .modules.media.router import router as media_router
from .modules.providers.router import router as providers_router
from .modules.research.router import router as research_router
from .modules.search.router import router as search_router
from .modules.tenancy.router import router as tenancy_router
from .modules.transcription.router import router as transcription_router
from .modules.tree.router import router as tree_router
from .settings import settings
from .db.session import SessionLocal

# Each functional module exposes an APIRouter named `router`, mounted under /api.
MODULE_ROUTERS = [
    auth_router,
    tenancy_router,
    tree_router,
    documents_router,
    jobs_router,
    providers_router,
    research_router,
    collaboration_router,
    interop_router,
    transcription_router,
    search_router,
    extraction_router,
    linkage_router,
    geo_router,
    sources_router,
    storage_audit_router,
    storage_quota_router,
    media_router,
    api_keys_router,
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await storage.ensure_buckets()
    await storage.check_ready()
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

    docs_url = "/docs" if settings.api_docs_enabled or settings.environment.lower() == "development" else None
    app = FastAPI(title="gen_suite API", version=settings.app_version, lifespan=lifespan,
                  docs_url=docs_url, redoc_url=None, openapi_url="/openapi.json" if docs_url else None)
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

    @app.get("/healthz", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/health", tags=["meta"], include_in_schema=False)
    async def legacy_health() -> dict:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/readyz", tags=["meta"])
    async def ready() -> dict:
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
            await events.get_redis().ping()
            await storage.check_ready()
        except Exception as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "servicios no disponibles") from exc
        return {"status": "ready", "version": settings.app_version}

    for router in MODULE_ROUTERS:
        if router is interop_router and not settings.gramps_sync_enabled and settings.environment.lower() != "development":
            continue
        app.include_router(router, prefix="/api")

    connectors = discover()
    connectors.mount(app, settings)

    @app.get("/api/connectors", tags=["meta"])
    async def list_connectors() -> dict:
        return {"enabled": connectors.capabilities(settings)}

    @app.get("/api/capabilities", tags=["meta"])
    async def capabilities() -> dict:
        """Public product capabilities, not configuration details or credentials.

        Clients use this to avoid advertising an installed integration when the server has
        deliberately disabled it.  Archive links are intentionally marked manual: the suite
        never claims it searched a third-party archive merely by opening its URL.
        """
        enabled = {item.get("name") for item in connectors.capabilities(settings)}
        return {
            "familysearch": settings.fs_connector_enabled and "familysearch" in enabled,
            "gramps_sync": settings.gramps_sync_enabled,
            "external_archive_search": "manual_links",
        }

    return app


app = create_app()
