from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.queue import get_queue
from ...core.security import Principal
from ...models.document import Document
from ...models.job import Job
from ...models.membership import MembershipRole
from ...modules.providers.service import ProviderService, embed_texts
from ..jobs.schemas import JobOut
from . import service
from .schemas import RecordHit, SearchHit, SuggestionOut

router = APIRouter(prefix="/search", tags=["search"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


@router.get("", response_model=list[SearchHit])
async def search(
    q: str = Query(min_length=1),
    mode: str = Query("hybrid", pattern="^(keyword|semantic|hybrid)$"),
    scope: str = Query("all", pattern="^(tenant|public|all)$"),
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[SearchHit]:
    qvec = None
    if mode in ("semantic", "hybrid"):
        rc = await ProviderService(db).resolve(tenant_id=principal.tenant_id, task_type="embedding")
        qvec = (await asyncio.to_thread(embed_texts, rc, [q]))[0]

    if mode == "keyword":
        hits = await service.keyword_search(db, q, scope, principal.tenant_id, limit)
    elif mode == "semantic":
        hits = await service.vector_search(db, qvec, scope, principal.tenant_id, limit)
    else:
        hits = await service.hybrid_search(db, q, qvec, scope, principal.tenant_id, limit)
    return [SearchHit(**h) for h in hits]


@router.get("/records", response_model=list[RecordHit])
async def search_records(
    q: str | None = Query(None),
    given: str | None = Query(None),
    surname: str | None = Query(None),
    record_type: str | None = Query(None),
    place: str | None = Query(None),
    year_from: int | None = Query(None),
    year_to: int | None = Query(None),
    role: str | None = Query(None),
    document_id: uuid.UUID | None = Query(None),
    fuzzy: bool = Query(True),
    semantic: bool = Query(False),
    scope: str = Query("all", pattern="^(tenant|public|all)$"),
    limit: int = Query(40, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[RecordHit]:
    """Structured search over extracted acts (record_type/date/place/name/role/book). Fuzzy by
    default (trigram + phonetic, tolerant to HTR errors); optional free-text (operators honoured) or
    semantic ordering."""
    qvec = None
    if semantic and q and q.strip():
        rc = await ProviderService(db).resolve(tenant_id=principal.tenant_id, task_type="embedding")
        qvec = (await asyncio.to_thread(embed_texts, rc, [q]))[0]
    hits = await service.search_records(
        db, given=given, surname=surname, record_type=record_type, place=place,
        year_from=year_from, year_to=year_to, role=role, document_id=document_id,
        q=q, qvec=qvec, fuzzy=fuzzy, scope=scope, tenant_id=principal.tenant_id, limit=limit,
    )
    return [RecordHit(**h) for h in hits]


@router.get("/suggest", response_model=list[SuggestionOut])
async def suggest(
    field: str = Query(..., pattern="^(surname|given|place)$"),
    q: str = Query(min_length=2),
    limit: int = Query(8, ge=1, le=20),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[SuggestionOut]:
    """'Did you mean?' suggestions for a name/place field (trigram + phonetic)."""
    rows = await service.suggest_terms(db, field=field, q=q, limit=limit)
    return [SuggestionOut(**r) for r in rows]


@router.post(
    "/embed/{document_id}", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def embed_document(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    job = Job(
        tenant_id=principal.tenant_id, type="embedding", status="queued",
        params={"document_id": str(document_id)}, created_by=principal.user_id,
    )
    db.add(job)
    await db.flush()
    queue = await get_queue()
    await queue.enqueue_job(
        "embed_document", job_id=str(job.id), tenant_id=str(principal.tenant_id),
        document_id=str(document_id),
    )
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at,
    )
