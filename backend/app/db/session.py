"""Async engine + session factory (connects as the restricted app role)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..settings import settings

engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.debug and settings.environment == "development",
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
