"""Audited operator cancellation of tickets that have never started an S3 request.

No object reads/writes/deletes and no paid dispatch. Sending/unknown tickets are
ineligible even if HEAD would currently return 404. Request transaction owns commit.
"""
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...models.ai_call import AiCall
from ...models.storage_quota import StorageWrite, StorageWriteAction
from ...models.tenant import Tenant
from ..providers.response_guard import review_lock
from . import inventory, quota

router = APIRouter()
ADMIN = [Depends(require_roles("tenant_admin"))]


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=10, max_length=2000)


class Approval(Proposal):
    client_id: uuid.UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorize: Literal[True]
    acknowledge_late_response_loss: bool = Field(default=False, strict=True)

    @field_validator("authorize", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True: raise ValueError("Confirma explícitamente la cancelación de esta reserva.")
        return value


async def owned(db, tenant_id, write_id, *, lock=False):
    stmt = select(StorageWrite).where(StorageWrite.id == write_id, StorageWrite.tenant_id == tenant_id)
    write = await db.scalar(stmt.with_for_update() if lock else stmt)
    if not write: raise HTTPException(404, "No existe ese recibo en este espacio.")
    return write


async def preview_data(db, tenant_id, write, reason):
    if write.state != "reserved" or write.upload_id or write.actual_bytes is not None or write.version_id:
        raise HTTPException(409, "Solo se cancela una reserva que no ha iniciado S3; un resultado enviado o incierto se conserva.")
    _, digest = quota.scope(write.bucket, write.storage_key)
    if write.location_hash != digest:
        raise HTTPException(409, "El recibo pertenece a otro destino. No se libera como si correspondiera al almacenamiento actual.")
    call = await db.scalar(select(AiCall).where(AiCall.tenant_id == tenant_id, AiCall.response_write_id == write.id))
    if call and (write.ai_guard_version != 1 or call.state != "reconciled" or call.result_key):
        raise HTTPException(409, "La respuesta de IA exige guarda de ejecución y conciliación económica previa sin resultado guardado. No se libera esta reserva.")
    result = dict(write_id=str(write.id), kind=write.kind, state=write.state, reserved_bytes=write.reserved_bytes,
        job_id=str(write.job_id) if write.job_id else None, ai_call_id=str(call.id) if call else None,
        ai_state=call.state if call else None, requires_late_response_loss_ack=call is not None,
        reason=reason, storage_changed=False, job_cancelled=False)
    # Includes private scope in the fingerprint without returning bucket/key to the UI.
    result["preview_hash"] = inventory.fingerprint({**result, "tenant_id": str(tenant_id),
        "updated_at": write.updated_at.isoformat(), "location_hash": write.location_hash,
        "ai_guard_version": write.ai_guard_version,
        "ai_updated_at": call.updated_at.isoformat() if call else None})
    return result


def action_out(row):
    return dict(id=row.id, write_id=row.write_id, created_by=row.created_by, created_at=row.created_at,
        reason=row.reason, released_bytes=row.released_bytes, evidence=row.evidence)


@router.post("/writes/{write_id}/cancel-preview", dependencies=ADMIN)
async def preview(write_id: uuid.UUID, body: Proposal, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return await preview_data(db, principal.tenant_id, await owned(db, principal.tenant_id, write_id), body.reason)


@router.post("/writes/{write_id}/cancel-unused", dependencies=ADMIN)
async def cancel(write_id: uuid.UUID, body: Approval, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    # Same order as financial admission: tenant -> quota -> receipt. The advisory
    # review lock is nonblocking, so a live provider is never waited on under row locks.
    await db.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    account = await quota.account(db, principal.tenant_id)
    action_id = uuid.uuid5(principal.tenant_id, "storage-cancel:v1:" + str(body.client_id))
    digest = inventory.fingerprint({"write_id": str(write_id), **body.model_dump(mode="json")})
    previous = await db.get(StorageWriteAction, action_id)
    if previous:
        if previous.request_hash != digest: raise HTTPException(409, "La misma solicitud no puede cambiar de recibo, motivo o confirmación.")
        return action_out(previous)
    if account.phase == "counting": raise HTTPException(409, "Espera a que termine el recuento protegido antes de modificar reservas.")
    write = await owned(db, principal.tenant_id, write_id, lock=True)
    proposed = await preview_data(db, principal.tenant_id, write, body.reason)
    if proposed["preview_hash"] != body.preview_hash:
        raise HTTPException(409, "La reserva o la conciliación cambiaron. Revisa una comparación nueva antes de confirmar.")
    if proposed["ai_call_id"]:
        await review_lock(db, write.id)
        if not body.acknowledge_late_response_loss:
            raise HTTPException(409, "Confirma que renuncias a guardar una respuesta tardía de esta llamada ya conciliada.")
    if not await quota.cancel_unused(db, principal.tenant_id, write.id):
        raise HTTPException(409, "La escritura ya ha empezado; su reserva no se libera.")
    action = StorageWriteAction(id=action_id, tenant_id=principal.tenant_id, write_id=write.id,
        created_by=principal.user_id, request_hash=digest, reason=body.reason, released_bytes=proposed["reserved_bytes"],
        evidence={**proposed, "after_state": "cancelled", "late_response_loss_ack": body.acknowledge_late_response_loss})
    db.add(action); await db.flush()
    return action_out(action)


@router.get("/actions")
async def history(offset: int = Query(0, ge=0), principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    rows = (await db.scalars(select(StorageWriteAction).where(StorageWriteAction.tenant_id == principal.tenant_id)
        .order_by(StorageWriteAction.created_at.desc(), StorageWriteAction.id).offset(offset).limit(20))).all()
    return [action_out(row) for row in rows]
