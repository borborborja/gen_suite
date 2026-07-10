from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import events as _events
from app.core import queue as _queue
from app.db.session import engine as app_engine
from app.main import app
from app.settings import settings


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Truncate identity tables before each test (uses the admin role, bypassing RLS), and clear
    the per-IP rate-limit buckets — every test registers from the same client IP, so with a live
    Redis the auth limiter would otherwise start returning 429 after a few tests."""
    admin = create_async_engine(settings.admin_database_url)
    async with admin.begin() as conn:
        await conn.execute(
            text("TRUNCATE memberships, refresh_tokens, tenants, users RESTART IDENTITY CASCADE;")
        )
    await admin.dispose()
    try:
        r = _events.get_redis()
        keys = [k async for k in r.scan_iter("rl:*")]
        if keys:
            await r.delete(*keys)
    except Exception:
        pass  # no Redis (fail-open limiter) → nothing to clear
    yield
    # pytest-asyncio uses a fresh event loop per test; drop pooled connections (DB + ARQ
    # Redis pool) so the next test never reuses one bound to a now-closed loop.
    await app_engine.dispose()
    for module, attr in ((_queue, "_pool"), (_events, "_redis")):
        client = getattr(module, attr)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
            setattr(module, attr, None)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
