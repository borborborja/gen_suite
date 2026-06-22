from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from . import service

router = APIRouter(tags=["media"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


class MediaOut(BaseModel):
    id: uuid.UUID
    person_id: uuid.UUID
    caption: str | None
    is_primary: bool


class MediaPatch(BaseModel):
    caption: str | None = None
    make_primary: bool = False


def _out(m) -> MediaOut:
    return MediaOut(id=m.id, person_id=m.person_id, caption=m.caption, is_primary=m.is_primary)


@router.post("/persons/{person_id}/media", response_model=MediaOut, dependencies=[Depends(require_roles(*_WRITE))])
async def upload_media(
    person_id: uuid.UUID, file: UploadFile = File(...), caption: str | None = Form(None),
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> MediaOut:
    data = await file.read()
    m = await service.upload_media(db, principal.tenant_id, person_id, data, file.content_type, caption)
    return _out(m)


@router.get("/persons/{person_id}/media", response_model=list[MediaOut])
async def list_media(person_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> list[MediaOut]:
    return [_out(m) for m in await service.list_media(db, person_id)]


@router.get("/media/{media_id}/raw")
async def media_raw(media_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> Response:
    data, content_type = await service.get_media_blob(db, media_id)
    return Response(content=data, media_type=content_type)


@router.patch("/media/{media_id}", response_model=MediaOut, dependencies=[Depends(require_roles(*_WRITE))])
async def patch_media(
    media_id: uuid.UUID, body: MediaPatch, db: AsyncSession = Depends(get_tenant_db),
) -> MediaOut:
    m = await service.update_media(db, media_id, caption=body.caption, make_primary=body.make_primary)
    return _out(m)


@router.delete("/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles(*_WRITE))])
async def delete_media(media_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> Response:
    await service.delete_media(db, media_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
