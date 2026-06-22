from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.queue import get_queue
from ...core.security import Principal
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.mention import PersonMention
from ...models.record import Record
from ..jobs.schemas import JobOut
from ..tree.service import _summaries
from . import dedup, service
from .schemas import (
    AcceptedOut, CandidateOut, DecisionOut, DiscoverRequest, MentionOut, ProposalOut, RecordOut,
    TreePersonOut,
)
from .service import ROLE_RELATION

router = APIRouter(prefix="/linkage", tags=["linkage"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id, type=job.type, status=job.status, progress=job.progress, result=job.result,
        error=job.error, created_at=job.created_at, started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post(
    "/discover", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def discover(
    body: DiscoverRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    job = Job(
        tenant_id=principal.tenant_id, type="linkage", status="queued",
        params={"person_id": str(body.person_id), "max_candidates": body.max_candidates},
        created_by=principal.user_id,
    )
    db.add(job)
    await db.commit()  # durable before enqueue so the worker can't race ahead of the row
    queue = await get_queue()
    await queue.enqueue_job(
        "generate_candidates", job_id=str(job.id), tenant_id=str(principal.tenant_id),
        person_id=str(body.person_id), max_candidates=body.max_candidates,
    )
    return _job_out(job)


@router.post(
    "/discover-family", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def discover_family(
    body: DiscoverRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobOut:
    """Discover the person's SIBLING SET (other baptisms with the same parents) → siblings + the
    parents they confirm. Mirrors /discover but uses the parent-pair (couple-key) search."""
    job = Job(
        tenant_id=principal.tenant_id, type="linkage_family", status="queued",
        params={"person_id": str(body.person_id), "max_candidates": body.max_candidates},
        created_by=principal.user_id,
    )
    db.add(job)
    await db.commit()
    queue = await get_queue()
    await queue.enqueue_job(
        "generate_family_candidates", job_id=str(job.id), tenant_id=str(principal.tenant_id),
        person_id=str(body.person_id), max_candidates=body.max_candidates,
    )
    return _job_out(job)


# ── Super-discovery: reconstruct the tree from the corpus (proposal) ──

class ReconstructRequest(BaseModel):
    conservative: bool = True
    include_census: bool = False
    link_to_tree: bool = True


class ReconstructionOut(BaseModel):
    id: uuid.UUID
    status: str
    conservative: bool
    include_census: bool
    link_to_tree: bool
    graph: dict | None
    stats: dict | None
    job_id: uuid.UUID | None


class MergeRequest(BaseModel):
    family_keys: list[str] | None = None


@router.post("/reconstruct", response_model=ReconstructionOut, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def reconstruct(
    body: ReconstructRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> ReconstructionOut:
    """Launch a corpus-wide tree reconstruction. Produces a reviewable proposal (not merged)."""
    from ...models.reconstruction import Reconstruction
    recon = Reconstruction(
        tenant_id=principal.tenant_id, status="running", conservative=body.conservative,
        include_census=body.include_census, link_to_tree=body.link_to_tree,
    )
    db.add(recon)
    await db.flush()
    job = Job(tenant_id=principal.tenant_id, type="reconstruction", status="queued",
              params={"reconstruction_id": str(recon.id)}, created_by=principal.user_id)
    db.add(job)
    await db.flush()
    recon.job_id = job.id
    await db.commit()  # durable before enqueue so the worker can't race ahead of the rows
    queue = await get_queue()
    await queue.enqueue_job(
        "reconstruct_tree", job_id=str(job.id), tenant_id=str(principal.tenant_id),
        reconstruction_id=str(recon.id), conservative=body.conservative,
        include_census=body.include_census, link_to_tree=body.link_to_tree,
    )
    return ReconstructionOut(id=recon.id, status=recon.status, conservative=recon.conservative,
                             include_census=recon.include_census, link_to_tree=recon.link_to_tree,
                             graph=None, stats=None, job_id=job.id)


@router.get("/reconstruction/latest", response_model=ReconstructionOut | None)
async def latest_reconstruction(db: AsyncSession = Depends(get_tenant_db)) -> ReconstructionOut | None:
    from ...models.reconstruction import Reconstruction
    r = await db.scalar(select(Reconstruction).order_by(Reconstruction.created_at.desc()).limit(1))
    if not r:
        return None
    return ReconstructionOut(id=r.id, status=r.status, conservative=r.conservative,
                             include_census=r.include_census, link_to_tree=r.link_to_tree,
                             graph=r.graph, stats=r.stats, job_id=r.job_id)


@router.post("/reconstruction/{recon_id}/merge", dependencies=[Depends(require_roles(*_WRITE))])
async def merge_reconstruction(
    recon_id: uuid.UUID, body: MergeRequest,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Materialize the proposed reconstruction (all of it, or selected families) into the tree."""
    from ...models.reconstruction import Reconstruction
    from .reconstruct import merge_reconstruction as _merge
    r = await db.get(Reconstruction, recon_id)
    if not r or not r.graph:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reconstruction not found or empty")
    from datetime import datetime, timezone
    result = await _merge(db, principal.tenant_id, r.graph, family_keys=body.family_keys)
    r.merged_at = datetime.now(timezone.utc)
    return result


def _mention_out(m: PersonMention) -> MentionOut:
    return MentionOut(id=m.id, role=m.role, name_raw=m.name_raw, given=m.given, surname=m.surname)


@router.get("/candidates", response_model=list[CandidateOut])
async def candidates(
    person_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_tenant_db),
) -> list[CandidateOut]:
    rows = await service.list_candidates(db, person_id, status_filter, limit, offset)
    summaries = await _summaries(db, {mc.tree_person_id for mc in rows})
    out: list[CandidateOut] = []
    for mc in rows:
        mention = await db.get(PersonMention, mc.person_mention_id)
        record = await db.get(Record, mc.record_id) if mc.record_id else None
        rec_out = None
        if record:
            co = await service._co_mentions(db, {record.id})
            # resolve the page number + manuscript folio so the UI shows real provenance
            page_no = None
            folio_label = None
            if record.page_id:
                from ...models.document import Page
                row = (await db.execute(
                    select(Page.page_no, Page.folio_label).where(Page.id == record.page_id)
                )).first()
                if row:
                    page_no, folio_label = row
            if page_no is None and record.transcription_id:
                from ...models.transcription import Transcription
                page_no = await db.scalar(
                    select(Transcription.page_no).where(Transcription.id == record.transcription_id))
            rec_out = RecordOut(
                id=record.id, record_type=record.record_type, date_raw=record.date_raw,
                date_year=record.date_year, summary=record.summary, parish_raw=record.parish_raw,
                transcription_id=record.transcription_id, page_id=record.page_id,
                document_id=record.document_id, page_no=page_no, folio_label=folio_label,
                confidence=record.confidence,
                mentions=[_mention_out(x) for x in co.get(record.id, [])],
            )
        tp = summaries.get(mc.tree_person_id)
        out.append(CandidateOut(
            id=mc.id, tree_person_id=mc.tree_person_id, person_mention_id=mc.person_mention_id,
            record_id=mc.record_id, score=mc.score, status=mc.status, method=mc.method,
            relation=getattr(mc, "relation", "self"),
            evidence=mc.evidence, record=rec_out,
            mention=_mention_out(mention) if mention else None,
            tree_person=TreePersonOut(
                id=tp.id, given=tp.given, surname=tp.surname,
                birth_year=tp.birth_year, death_year=tp.death_year,
            ) if tp else None,
            created_at=mc.created_at,
        ))
    return out


@router.post(
    "/candidates/{candidate_id}/confirm", response_model=DecisionOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def confirm(
    candidate_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DecisionOut:
    mc = await service.confirm_candidate(db, candidate_id, principal.user_id)
    return DecisionOut(id=mc.id, status=mc.status, resolved_person_id=mc.tree_person_id)


@router.post(
    "/candidates/{candidate_id}/reject", response_model=DecisionOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def reject(
    candidate_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DecisionOut:
    mc = await service.reject_candidate(db, candidate_id, principal.user_id)
    return DecisionOut(id=mc.id, status=mc.status)


@router.get("/mentions/{mention_id}/coreferents")
async def coreferents(
    mention_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[dict]:
    """Within-corpus entity resolution (M4): other acts that mention the SAME person."""
    return await dedup.find_coreferents(db, principal.tenant_id, mention_id)


@router.get("/documents/{document_id}/duplicate-records")
async def duplicate_records(
    document_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[dict]:
    """Within-corpus dedup (M4): groups of records that look like the same act extracted twice."""
    return await dedup.find_duplicate_records(db, principal.tenant_id, document_id)


@router.get("/candidates/{candidate_id}/proposals", response_model=list[ProposalOut])
async def proposals(
    candidate_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> list[ProposalOut]:
    return [
        ProposalOut(
            mention_id=m.id, role=m.role, name_raw=m.name_raw, given=m.given, surname=m.surname,
            suggested_relation=ROLE_RELATION.get(m.role, ("other", None))[0],
        )
        for m in await service.list_proposals(db, candidate_id)
    ]


@router.post(
    "/candidates/{candidate_id}/proposals/{mention_id}/accept", response_model=AcceptedOut,
    status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles(*_WRITE))],
)
async def accept_proposal(
    candidate_id: uuid.UUID,
    mention_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> AcceptedOut:
    person = await service.accept_proposal(
        db, principal.tenant_id, candidate_id, mention_id, principal.user_id
    )
    return AcceptedOut(person_id=person.id, mention_id=mention_id)
