from __future__ import annotations

import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...models.job import Job
from ...models.storage_inventory import StorageScan
from . import service

router = APIRouter(prefix="/storage/inventories", tags=["storage-inventory"])
ADMIN = [Depends(require_roles("tenant_admin"))]


class NewInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    client_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


@router.get("")
async def listing(offset: int = Query(0, ge=0), principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    rows = (await db.execute(select(StorageScan, Job).join(Job, Job.id == StorageScan.job_id).where(
        StorageScan.tenant_id == principal.tenant_id, Job.type == "inventory_storage"
        ).order_by(StorageScan.created_at.desc(), StorageScan.id.desc()).offset(offset).limit(20))).all()
    return [service.summary(scan, job) for scan, job in rows]


@router.get("/{scan_id}")
async def detail(scan_id: uuid.UUID, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return await service.scan_out(db, await service.owned(db, principal.tenant_id, scan_id))


@router.post("", status_code=202, dependencies=ADMIN)
async def create(body: NewInventory, principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return await service.create(db, principal, body)


@router.post("/{scan_id}/{action}", status_code=202, dependencies=ADMIN)
async def control(scan_id: uuid.UUID, action: Literal["retry", "cancel"],
                  principal=Depends(get_current_principal), db=Depends(get_tenant_db)):
    return await service.control(db, principal.tenant_id, scan_id, action)
