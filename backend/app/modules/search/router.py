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
from ...modules.providers.service import embed_texts_with_usage
from ..jobs.schemas import JobOut
from . import service
from .schemas import RecordHit, SearchHit, SuggestionOut
from .index_router import router as index_router
from .indexes import query_context

router = APIRouter(prefix="/search", tags=["search"])
router.include_router(index_router)

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
    qvec = None; generation = None; usage = {}
    if mode in ("semantic", "hybrid"):
        generation, rc = await query_context(db, principal.tenant_id)
        vectors, usage = await embed_texts_with_usage(rc, [q]); qvec = vectors[0]

    if mode == "keyword":
        hits = await service.keyword_search(db, q, scope, principal.tenant_id, limit)
    elif mode == "semantic":
        hits = await service.vector_search(db, qvec, scope, principal.tenant_id, limit,
            generation_id=generation.id, query_call_id=usage.get("ai_call_id"))
    else:
        hits = await service.hybrid_search(db, q, qvec, scope, principal.tenant_id, limit,
            generation_id=generation.id, query_call_id=usage.get("ai_call_id"))
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
    qvec = None; generation = None; usage = {}
    if semantic and q and q.strip():
        generation, rc = await query_context(db, principal.tenant_id)
        vectors, usage = await embed_texts_with_usage(rc, [q]); qvec = vectors[0]
    hits = await service.search_records(
        db, given=given, surname=surname, record_type=record_type, place=place,
        year_from=year_from, year_to=year_to, role=role, document_id=document_id,
        q=q, qvec=qvec, fuzzy=fuzzy, scope=scope, tenant_id=principal.tenant_id, limit=limit,
        generation_id=generation.id if generation else None, query_call_id=usage.get("ai_call_id"),
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
    from sqlalchemy import select
    from fastapi import HTTPException
    from ...core.dispatch import schedule
    from ..jobs.service import active_job_for
    if not await db.scalar(select(Document.id).where(Document.id == document_id,
        Document.tenant_id == principal.tenant_id).with_for_update()):
        raise HTTPException(404, "Documento no encontrado.")
    existing = await active_job_for(db, principal.tenant_id, "embedding", document_id)
    if existing: return JobOut.model_validate(existing, from_attributes=True)
    job = Job(
        tenant_id=principal.tenant_id, type="embedding", status="queued",
        params={"document_id": str(document_id)}, created_by=principal.user_id,
    )
    db.add(job)
    await schedule(db, job, "embed_document", document_id=str(document_id))
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at,
    )
