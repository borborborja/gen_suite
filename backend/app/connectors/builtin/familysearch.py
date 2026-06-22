"""FamilySearch connector — GATED by FS_CONNECTOR_ENABLED (off by default).

FamilySearch does not permit programmatic downloading, so this connector is only registered
(routes mounted, capability advertised) when the server operator sets the env flag. Session
cookies are operator-owned (server-admin), stored encrypted; downloads land as tenant-PRIVATE
documents that can never be published (enforced in the documents module).
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.crypto import get_secret_box
from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.queue import get_queue
from ...core.security import Principal
from ...models.connector import ConnectorCredential
from ...models.job import Job
from ...models.membership import MembershipRole
from ..base import Connector

router = APIRouter(prefix="/familysearch", tags=["familysearch"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


def validate_fs_url(url: str) -> str:
    """Only allow https URLs on familysearch.org — without this a caller could point the server
    at internal services / cloud-metadata (SSRF), since the worker fetches whatever URL is given."""
    from urllib.parse import urlparse

    try:
        u = urlparse(url.strip())
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "URL inválida")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not (host == "familysearch.org" or host.endswith(".familysearch.org")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "la URL debe ser https://…familysearch.org")
    return url.strip()


class FsDownloadRequest(BaseModel):
    url: str
    max_images: int | None = None
    delay: float | None = None


class FsCredentialCreate(BaseModel):
    label: str
    cookies_json: str  # exported browser cookies (must contain fssessionid)


def _require_server_admin(principal: Principal) -> None:
    if not principal.is_server_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "server-admin required")


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_roles(*_WRITE))])
async def start_download(
    body: FsDownloadRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    url = validate_fs_url(body.url)
    job = Job(
        tenant_id=principal.tenant_id, type="fs_download", status="queued",
        params={"url": url}, created_by=principal.user_id,
    )
    db.add(job)
    await db.commit()  # persist before enqueue so the worker can never race ahead of the row
    queue = await get_queue()
    await queue.enqueue_job(
        "fs_download", job_id=str(job.id), tenant_id=str(principal.tenant_id), url=url,
        settings={"max_images": body.max_images, "delay": body.delay},
    )
    return {"id": str(job.id), "status": job.status}


@router.get("/credentials")
async def list_credentials(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[dict]:
    _require_server_admin(principal)
    rows = (
        await db.scalars(
            select(ConnectorCredential).where(ConnectorCredential.connector == "familysearch")
        )
    ).all()
    return [{"id": str(c.id), "label": c.label, "is_active": c.is_active} for c in rows]


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
async def add_credential(
    body: FsCredentialCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    _require_server_admin(principal)
    try:
        json.loads(body.cookies_json)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cookies_json must be valid JSON")
    ciphertext, nonce = get_secret_box().encrypt(body.cookies_json)
    cred = ConnectorCredential(
        connector="familysearch", label=body.label, secret_ciphertext=ciphertext,
        secret_nonce=nonce, created_by=principal.user_id,
    )
    db.add(cred)
    await db.flush()
    return {"id": str(cred.id), "label": cred.label}


@router.delete("/credentials/{cred_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    cred_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> None:
    _require_server_admin(principal)
    cred = await db.get(ConnectorCredential, cred_id)
    if cred:
        await db.delete(cred)


class FamilySearchConnector(Connector):
    name = "familysearch"
    scope = "server"
    requires_env_flag = "fs_connector_enabled"

    def router(self) -> APIRouter:
        return router

    def capabilities(self) -> dict:
        return {"name": self.name, "scope": self.scope, "actions": ["download"]}
