"""Exclude operator release/reconciliation while a new paid response is being produced.

The session lock proves a live DB owner, not universal remote cancellation. The
durable financial state is checked again before dispatch; legacy calls have no guard.
"""
import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from fastapi import HTTPException
from sqlalchemy import select, text
from ...db.session import engine, SessionLocal
from ...db.rls import set_rls_context
from ...models.ai_call import AiCall
from ...models.storage_quota import StorageWrite


def lock_key(write_id):
    return int.from_bytes(hashlib.sha256(f"ai-response:v1:{uuid.UUID(str(write_id))}".encode()).digest()[:8], "big", signed=True)


async def review_lock(db, write_id):
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(write_id)}):
        raise HTTPException(409, "La respuesta de IA sigue en ejecución; no se retira su reserva ni se concilia todavía.")


async def review_call(db, call):
    if call.response_write_id:
        write = await db.get(StorageWrite, call.response_write_id)
        if write and write.ai_guard_version == 1:
            await review_lock(db, write.id)


async def validate_dispatch(tenant_id, call_id, write_id):
    async with SessionLocal() as db:
        await set_rls_context(db, tenant_id=tenant_id)
        call = await db.get(AiCall, call_id)
        write = await db.get(StorageWrite, write_id)
        if (not call or call.tenant_id != tenant_id or call.state != "inflight" or call.response_write_id != write_id
                or not write or write.tenant_id != tenant_id or write.state != "reserved" or write.ai_guard_version != 1):
            raise HTTPException(409, "La llamada o su reserva cambiaron antes de enviar a IA. No se inicia otra llamada.")


@asynccontextmanager
async def hold(tenant_id, call_id, write_id):
    key = lock_key(write_id)
    async with engine.connect() as connection:
        acquired = None
        try:
            acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            await connection.commit()
            if not acquired:
                raise HTTPException(409, "Otro intento conserva el control de esta respuesta de IA.")
            await validate_dispatch(tenant_id, call_id, write_id)
            yield
        finally:
            if acquired is not False:
                async def release():
                    try:
                        await connection.rollback()
                        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                        await connection.commit()
                    except BaseException:
                        # Never return a possibly locked physical connection to the pool.
                        await connection.invalidate()
                        raise
                cleanup = asyncio.create_task(release())
                try: await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup  # Finish cleanup before the connection context closes.
                    raise
