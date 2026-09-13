"""Serialize redelivery of a durable job without holding a database transaction open."""
from __future__ import annotations

import asyncio
import functools
import uuid

from sqlalchemy import text
from ..db.session import engine
from ..modules.jobs.constants import TERMINAL_STATUSES
from .execution_context import current_job


def serialized_job(function):
    @functools.wraps(function)
    async def wrapped(ctx, *, job_id, tenant_id, **kwargs):
        jid, tid = uuid.UUID(str(job_id)), uuid.UUID(str(tenant_id))
        key = int.from_bytes(jid.bytes[:8], "big", signed=True)
        async with engine.connect() as connection:
            locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            await connection.commit()
            if not locked:
                return
            try:
                await connection.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tid)})
                state = await connection.scalar(text("SELECT status FROM jobs WHERE id = :job AND tenant_id = :tenant"), {"job": jid, "tenant": tid})
                await connection.commit()
                if state is None or state in TERMINAL_STATUSES:
                    return
                token = current_job.set((tid, jid))
                try:
                    return await function(ctx, job_id=jid, tenant_id=tid, **kwargs)
                finally:
                    current_job.reset(token)
            finally:
                async def release():
                    await connection.rollback()
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    await connection.commit()
                await asyncio.shield(release())
    return wrapped
