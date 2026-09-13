from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.queue import get_queue
from ...core.security import Principal
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.mention import PersonMention
from ...models.record import Record
from ...models.mutation_request import MutationRequest
from ...models.change_log import ChangeLog
from ...settings import settings
from ..jobs.schemas import JobOut
from ..tree.service import _summaries
from . import dedup, service
from .schemas import (
    AcceptedOut, CandidateApplyIn, CandidateApplyOut, CandidateOut, CandidatePreviewOut,
    DecisionOut, DiscoverRequest, MentionOut, ProposalOut, RecordOut, TreePersonOut,
)
from .service import ROLE_RELATION
from ..tree.audit import audited

router = APIRouter(prefix="/linkage", tags=["linkage"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


def _preview_token(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(settings.jwt_secret.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def _read_preview_token(token: str) -> dict:
    try:
        raw_part, sig_part = token.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_part + "=" * (-len(raw_part) % 4))
        actual = base64.urlsafe_b64decode(sig_part + "=" * (-len(sig_part) % 4))
        expected = hmac.new(settings.jwt_secret.encode(), raw, hashlib.sha256).digest()
        payload = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "previsualización inválida") from exc
    if not hmac.compare_digest(actual, expected) or int(payload.get("expires_at", 0)) < int(time.time()):
        raise HTTPException(status.HTTP_409_CONFLICT, "la previsualización ha caducado; vuelve a revisarla")
    return payload


def _request_hash(body: CandidateApplyIn) -> str:
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


async def _lock_tree(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    await db.execute(sa_text("SELECT pg_advisory_xact_lock(hashtext(:tenant_key))"), {
        "tenant_key": str(tenant_id),
    })


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
    from ..jobs.service import active_job_for
    existing = await active_job_for(
        db, principal.tenant_id, "linkage", body.person_id, param_key="person_id")
    if existing:
        return _job_out(existing)
    from ..providers.service import assert_within_budget
    await assert_within_budget(db, principal.tenant_id)
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
    from ..jobs.service import active_job_for
    existing = await active_job_for(
        db, principal.tenant_id, "linkage_family", body.person_id, param_key="person_id")
    if existing:
        return _job_out(existing)
    from ..providers.service import assert_within_budget
    await assert_within_budget(db, principal.tenant_id)
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
    from ..jobs.service import active_job_of_type
    existing = await active_job_of_type(db, principal.tenant_id, "reconstruction")
    if existing:  # a reconstruction is already running — return it instead of piling up another
        rec = await db.scalar(select(Reconstruction).where(Reconstruction.job_id == existing.id))
        if rec:
            return ReconstructionOut(
                id=rec.id, status=rec.status, conservative=rec.conservative,
                include_census=rec.include_census, link_to_tree=rec.link_to_tree,
                graph=None, stats=None, job_id=existing.id)
    from ..providers.service import assert_within_budget
    await assert_within_budget(db, principal.tenant_id)
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
    async with audited(db, principal, action="reconstruction_merge", entity_type="reconstruction",
                       entity_id=recon_id, summary="Fusionó una reconstrucción revisada") as capture:
        result = await _merge(db, principal.tenant_id, r.graph, family_keys=body.family_keys)
        r.merged_at = datetime.now(timezone.utc)
    return {**result, "change_id": str(capture.change_id) if capture.change_id else None}


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
            record_id=mc.record_id, score=mc.score, score_kind=mc.score_kind,
            score_version=mc.score_version, status=mc.status, method=mc.method,
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
    "/candidates/{candidate_id}/preview", response_model=CandidatePreviewOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def preview_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> CandidatePreviewOut:
    """Build a non-mutating, short-lived review of the exact tree decision."""
    preview = await service.preview_candidate(db, candidate_id)
    token_payload = {
        "candidate_id": preview["candidate_id"], "status": preview["status"],
        "updated_at": preview["updated_at"], "expires_at": int(time.time()) + 15 * 60,
    }
    return CandidatePreviewOut(**preview, preview_token=_preview_token(token_payload))


@router.post(
    "/candidates/{candidate_id}/apply", response_model=CandidateApplyOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def apply_candidate(
    candidate_id: uuid.UUID, body: CandidateApplyIn,
    idempotency_key: str | None = Header(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> CandidateApplyOut:
    """Apply a human-reviewed decision once, with an auditable server-side undo."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "se requiere la cabecera Idempotency-Key (máximo 128 caracteres)")
    payload = _read_preview_token(body.preview_token)
    if payload.get("candidate_id") != str(candidate_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "la previsualización no corresponde a esta propuesta")
    request_hash = _request_hash(body)
    await _lock_tree(db, principal.tenant_id)
    prior = await db.scalar(select(MutationRequest).where(
        MutationRequest.tenant_id == principal.tenant_id,
        MutationRequest.idempotency_key == idempotency_key,
    ))
    if prior:
        if prior.request_hash != request_hash:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "la clave de idempotencia ya se usó con otra decisión")
        if prior.change_id:
            prior_change = await db.get(ChangeLog, prior.change_id)
            if prior_change and prior_change.reverted_at:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "esa decisión fue deshecha; vuelve a previsualizarla")
        return CandidateApplyOut(**prior.response)

    from ...models.match_candidate import MatchCandidate
    candidate = await db.get(MatchCandidate, candidate_id)
    if (not candidate or candidate.status != payload.get("status") or
            candidate.updated_at.isoformat() != payload.get("updated_at")):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "la propuesta cambió; vuelve a previsualizarla antes de aplicarla")

    async with audited(db, principal, action="linkage_apply", entity_type="candidate",
                       entity_id=candidate_id, summary="Aplicó una propuesta revisada al árbol") as capture:
        candidate, people = await service.apply_reviewed_candidate(
            db, principal.tenant_id, candidate_id, principal.user_id,
            relative_mention_ids=body.relative_mention_ids,
            existing_person_ids=body.existing_person_ids,
            candidate_person_id=body.candidate_person_id,
        )
    if not capture.change_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "la propuesta no produjo ningún cambio")
    resolved_person_id = candidate.tree_person_id
    if candidate.relation != "self":
        mention = await db.get(PersonMention, candidate.person_mention_id)
        resolved_person_id = mention.resolved_person_id if mention else None
    response = {
        "id": str(candidate.id), "status": candidate.status,
        "resolved_person_id": str(resolved_person_id) if resolved_person_id else None,
        "created_inferred": [], "change_id": str(capture.change_id),
        "created_or_linked_person_ids": [str(person.id) for person in people if person],
    }
    # The ledger is committed with the tree change by get_tenant_db; a response can never
    # claim success unless the corresponding ChangeLog entry commits too.
    db.add(MutationRequest(tenant_id=principal.tenant_id, idempotency_key=idempotency_key,
                           request_hash=request_hash, response=response, change_id=capture.change_id))
    await db.flush()
    return CandidateApplyOut(**response)


@router.post(
    "/candidates/{candidate_id}/confirm", response_model=DecisionOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def confirm(
    candidate_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DecisionOut:
    raise HTTPException(status.HTTP_410_GONE,
                        "usa la previsualización y aplicación revisable de la propuesta")


@router.post(
    "/candidates/{candidate_id}/reject", response_model=DecisionOut,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def reject(
    candidate_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> DecisionOut:
    async with audited(db, principal, action="linkage_reject", entity_type="candidate",
                       entity_id=candidate_id, summary="Descartó una propuesta") as capture:
        mc = await service.reject_candidate(db, candidate_id, principal.user_id)
    return DecisionOut(id=mc.id, status=mc.status, change_id=capture.change_id)


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
    raise HTTPException(status.HTTP_410_GONE,
                        "selecciona los familiares en la aplicación revisable de la propuesta")
