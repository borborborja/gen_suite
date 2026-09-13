"""Research plans and controls. No paid work runs inline in an HTTP transaction."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, update

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.ai_call import AiCall
from ...models.document import Document, Page
from ...models.job import Job
from ...models.membership import MembershipRole
from ...models.person import Person
from ...models.research import ResearchProject
from ...models.research_run import ResearchRun, ResearchRunStage, ResearchRunEvent
from ...models.tenant import Tenant
from ..providers.admission import LOCAL_ENGINES, fingerprint, policy_for, snapshot, totals
from ..providers.service import ProviderService

router = APIRouter(prefix="/runs", tags=["research-runs"])
WRITE = [Depends(require_roles(MembershipRole.tenant_admin.value, MembershipRole.researcher.value))]

# One durable child per stage; no automatic writes to the accepted tree.
STAGE_TASKS = {
    "rasterize": ("rasterize", "rasterize_document"),
    "transcribe": ("transcription", "transcribe_document"),
    "extract": ("extraction", "extract_records"),
    "index_register": ("index", "parse_index"),
    "embed_text": ("embedding", "embed_document"),
    "embed_mentions": ("embed_mentions", "embed_mentions"),
    "embed_corpus": ("reembed_corpus", "reembed_corpus"),
    "discover": ("linkage", "generate_candidates"),
    "pdf": ("compact_pdf", "compact_to_pdf"),
    "archive": ("document_archive", "export_document_archive"),
}


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    project_id: uuid.UUID | None = None
    person_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10000)
    discover_all_tree: bool = True
    transcribe: bool = True
    extract: bool = True
    index: bool = True
    pdf: bool = True
    archive: bool = True
    budget_microusd: int = Field(ge=0, le=1_000_000_000_000, strict=True)
    lang: Literal["spa", "cat", "lat", "spa+cat+lat"] = "spa"

    @model_validator(mode="after")
    def check_selection(self):
        if len(set(self.document_ids)) != len(self.document_ids) or len(set(self.person_ids)) != len(self.person_ids):
            raise ValueError("La selección contiene identificadores duplicados")
        if self.person_ids and self.discover_all_tree:
            raise ValueError("Elige personas concretas o todo el árbol, no ambos")
        if not any((self.transcribe, self.extract, self.index, self.pdf, self.archive, self.person_ids, self.discover_all_tree)):
            raise ValueError("Selecciona al menos una etapa")
        return self


class RunAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    reason: str = Field(default="", max_length=2000)


class RunBudget(RunAction):
    budget_microusd: int = Field(ge=0, le=1_000_000_000_000, strict=True)


def append_event(session, run, kind, detail, actor=None):
    session.add(ResearchRunEvent(tenant_id=run.tenant_id, run_id=run.id,
        actor_id=actor, kind=kind, detail=detail))


async def run_balance(session, tenant_id, run_id):
    spent = int(await session.scalar(select(func.coalesce(func.sum(AiCall.cost_microusd), 0)).where(
        AiCall.tenant_id == tenant_id, AiCall.research_run_id == run_id,
        AiCall.state.in_(("succeeded", "reconciled")))) or 0)
    reserved = int(await session.scalar(select(func.coalesce(func.sum(AiCall.reserved_microusd), 0)).where(
        AiCall.tenant_id == tenant_id, AiCall.research_run_id == run_id,
        AiCall.state.in_(("inflight", "unknown")))) or 0)
    return {"spent_microusd": spent, "reserved_microusd": reserved}


async def get_run(session, tenant_id, run_id, *, lock=False):
    stmt = select(ResearchRun).where(ResearchRun.id == run_id, ResearchRun.tenant_id == tenant_id)
    if lock:
        # Match the order used by AI admission: tenant, then research execution.
        await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
        stmt = stmt.with_for_update()
    run = await session.scalar(stmt)
    if not run:
        raise HTTPException(404, "Investigación no encontrada.")
    return run


async def run_out(session, run, *, brief=False):
    counts = dict((await session.execute(select(ResearchRunStage.status, func.count()).where(
        ResearchRunStage.run_id == run.id).group_by(ResearchRunStage.status))).all())
    return {"id": run.id, "title": run.title, "status": run.status, "version": run.version,
        "project_id": run.project_id, "budget_microusd": run.budget_microusd,
        "profiles": {} if brief else run.profiles, "config": {} if brief else run.config, "error": run.error,
        "document_count": len(run.config.get("document_ids", [])), "person_count": len(run.config.get("person_ids", [])),
        "created_at": run.created_at, "finished_at": run.finished_at,
        "stage_counts": counts, **await run_balance(session, run.tenant_id, run.id)}


def stage_plan(documents, person_ids, body):
    plan = []
    for doc in documents:
        def add(kind):
            plan.append(dict(kind=kind, document_id=doc.id, person_id=None, title=doc.title))
        if doc.doc_type == "pdf": add("rasterize")
        if body.transcribe: add("transcribe")
        if body.extract: add("index_register" if doc.is_index else "extract")
        if body.index:
            add("embed_text")
            if not doc.is_index: add("embed_mentions")
        if body.pdf: add("pdf")
        if body.archive: add("archive")
    # Semantic discovery over the tenant corpus requires a compatible index of that corpus,
    # not only vectors for the newly selected books. Existing identical items are reused.
    if body.index and person_ids:
        plan.append(dict(kind="embed_corpus", document_id=None, person_id=None,
                         title="Completar índice compatible del corpus del espacio"))
    # Retrieval only starts once all selected books are prepared.
    for person_id in person_ids:
        plan.append(dict(kind="discover", document_id=None, person_id=person_id, title="Buscar familiares y evidencias"))
    return plan


async def freeze_profiles(session, tenant_id, tasks):
    profiles = {}
    for task in sorted(tasks):
        rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type=task)
        if rc.engine in LOCAL_ENGINES:
            profiles[task] = dict(engine=rc.engine, model=rc.model, base_url=None if rc.engine == "tesseract" else rc.base_url,
                credential_id=str(rc.credential_id) if rc.credential_id else None)
        else:
            policy_for(rc)  # Do not create a new research plan with an already expired tariff.
            profiles[task] = snapshot(rc)
    return profiles


@router.post("", status_code=201, dependencies=WRITE)
async def create_run(body: RunCreate, principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    rid = uuid.uuid5(principal.tenant_id, f"research-run:{body.client_id}")
    request_hash = fingerprint(body.model_dump(mode="json"))
    # Serialize the plan creation, including the idempotency check and overlapping selection.
    await session.scalar(select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update(key_share=True))
    existing = await session.get(ResearchRun, rid)
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "La petición ya existe con otra configuración.")
        return await run_out(session, existing)
    docs = list((await session.scalars(select(Document).where(Document.tenant_id == principal.tenant_id,
        Document.id.in_(body.document_ids)).order_by(Document.id))).all())
    if len(docs) != len(body.document_ids):
        raise HTTPException(404, "Selecciona únicamente documentos de tu espacio.")
    if body.project_id and not await session.scalar(select(ResearchProject.id).where(
        ResearchProject.id == body.project_id, ResearchProject.tenant_id == principal.tenant_id)):
        raise HTTPException(404, "Proyecto no encontrado.")
    overlap = await session.scalar(select(ResearchRunStage.id).join(ResearchRun,
        ResearchRunStage.run_id == ResearchRun.id).where(ResearchRun.tenant_id == principal.tenant_id,
        ResearchRun.status.notin_(("completed", "cancelled")), ResearchRunStage.document_id.in_(body.document_ids)).limit(1))
    if overlap:
        raise HTTPException(409, "Hay una investigación pendiente sobre alguno de estos libros. Reanúdala o cancélala primero.")
    busy = await session.scalar(select(Job.id).where(Job.tenant_id == principal.tenant_id,
        Job.status.in_(("queued", "running", "paused")), Job.params["document_id"].astext.in_([str(id) for id in body.document_ids])).limit(1))
    if busy:
        raise HTTPException(409, "Espera a que finalicen las tareas actuales de los documentos seleccionados.")
    if await session.scalar(select(Page.id).where(Page.document_id.in_(body.document_ids), Page.image_purged.is_(True)).limit(1)):
        raise HTTPException(409, "Algún original no está disponible; restáuralo antes de iniciar la investigación.")
    if body.discover_all_tree:
        person_ids = list((await session.scalars(select(Person.id).where(Person.tenant_id == principal.tenant_id)
                                                .order_by(Person.id).limit(10001))).all())
        if len(person_ids) > 10000:
            raise HTTPException(413, "Acota esta investigación a un máximo de 10.000 personas.")
    else:
        person_ids = list(body.person_ids)
        if person_ids and await session.scalar(select(func.count()).select_from(Person).where(
            Person.tenant_id == principal.tenant_id, Person.id.in_(person_ids))) != len(person_ids):
            raise HTTPException(404, "Alguna persona no pertenece al árbol de este espacio.")
    plan = stage_plan(docs, person_ids, body)
    if not plan:
        raise HTTPException(422, "No hay personas que comparar ni etapas documentales seleccionadas.")
    tasks = set()
    if body.transcribe: tasks.add("transcription")
    if body.extract or person_ids: tasks.add("inference")
    if body.index or person_ids: tasks.add("embedding")
    profiles = await freeze_profiles(session, principal.tenant_id, tasks)
    config = {**body.model_dump(mode="json", exclude={"client_id", "budget_microusd"}),
              "person_ids": [str(id) for id in person_ids], "pipeline_version": 2, "discovery_scope": "tenant_corpus",
              "documents": [{"id": str(doc.id), "title": doc.title, "fingerprint": doc.fingerprint,
                             "page_count_at_creation": doc.page_count} for doc in docs]}
    run = ResearchRun(id=rid, tenant_id=principal.tenant_id, title=body.title, project_id=body.project_id,
        created_by=principal.user_id, status="planned", version=1, budget_microusd=body.budget_microusd,
        request_hash=request_hash, config=config, profiles=profiles)
    session.add(run); await session.flush()
    for index, item in enumerate(plan):
        session.add(ResearchRunStage(id=uuid.uuid5(rid, f"stage:{index}"), tenant_id=principal.tenant_id,
            run_id=rid, position=index, status="pending", attempts=0, **item))
    append_event(session, run, "created", {"budget_microusd": run.budget_microusd,
        "profiles": profiles, "document_count": len(docs), "person_count": len(person_ids)}, principal.user_id)
    await session.flush()
    return await run_out(session, run)


@router.get("")
async def list_runs(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                    principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    runs = (await session.scalars(select(ResearchRun).where(ResearchRun.tenant_id == principal.tenant_id)
                                 .order_by(ResearchRun.created_at.desc(), ResearchRun.id).offset(offset).limit(limit))).all()
    return [await run_out(session, run, brief=True) for run in runs]


@router.get("/{run_id}")
async def detail(run_id: uuid.UUID, principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    return await run_out(session, await get_run(session, principal.tenant_id, run_id))


@router.get("/{run_id}/stages")
async def stages(run_id: uuid.UUID, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                 principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    await get_run(session, principal.tenant_id, run_id)
    rows = (await session.scalars(select(ResearchRunStage).where(ResearchRunStage.run_id == run_id)
                                  .order_by(ResearchRunStage.position).offset(offset).limit(limit))).all()
    return [{"id": row.id, "position": row.position, "kind": row.kind, "title": row.title,
        "status": row.status, "document_id": row.document_id, "person_id": row.person_id,
        "job_id": row.job_id, "attempts": row.attempts, "result": row.result, "error": row.error} for row in rows]


@router.get("/{run_id}/events")
async def history(run_id: uuid.UUID, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                  principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    await get_run(session, principal.tenant_id, run_id)
    return list((await session.scalars(select(ResearchRunEvent).where(ResearchRunEvent.run_id == run_id)
        .order_by(ResearchRunEvent.created_at.desc(), ResearchRunEvent.id).offset(offset).limit(limit))).all())


@router.post("/{run_id}/{action}", dependencies=WRITE)
async def control(run_id: uuid.UUID, action: Literal["start", "pause", "resume", "retry", "cancel"], body: RunAction,
                  principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    run = await get_run(session, principal.tenant_id, run_id, lock=True)
    if body.expected_version != run.version:
        raise HTTPException(409, "La investigación ha cambiado; actualiza su estado antes de continuar.")
    if run.status in ("completed", "cancelled"):
        raise HTTPException(409, "La investigación ya ha terminado.")
    allowed = {"start": {"planned"}, "pause": {"running"}, "resume": {"paused"},
               "retry": {"needs_attention"}, "cancel": {"planned", "running", "paused", "needs_attention"}}
    if run.status not in allowed[action]:
        raise HTTPException(409, "Esta acción no corresponde al estado actual de la investigación.")
    if action == "retry" and not body.reason.strip():
        raise HTTPException(422, "Indica qué se ha corregido antes de reintentar.")
    if action in {"start", "resume", "retry"}:
        balance = await totals(session, principal.tenant_id)
        if balance["unknown_calls"]:
            raise HTTPException(409, "Concilia las llamadas inciertas en Ajustes antes de continuar.")
        run.status = "running"; run.error = None
        if action == "retry":
            # Reuse the same child job and its paid response fingerprints.
            await session.execute(update(ResearchRunStage).where(ResearchRunStage.run_id == run.id,
                ResearchRunStage.status == "needs_attention").values(status="paused", error=None))
    else:
        run.status = "cancelled" if action == "cancel" else "paused"
        job_ids = select(ResearchRunStage.job_id).where(ResearchRunStage.run_id == run.id)
        await session.execute(update(Job).where(Job.tenant_id == run.tenant_id, Job.id.in_(job_ids),
            Job.status.in_(("queued", "running", "paused"))).values(
                status="cancelled" if action == "cancel" else "paused", finished_at=datetime.now(timezone.utc)))
        await session.execute(update(ResearchRunStage).where(ResearchRunStage.run_id == run.id,
            ResearchRunStage.status.in_(("pending", "running", "paused", "needs_attention"))).values(
                status="cancelled" if action == "cancel" else "paused"))
        if action == "cancel": run.finished_at = datetime.now(timezone.utc)
    run.version += 1
    append_event(session, run, action, {"reason": body.reason}, principal.user_id)
    await session.flush()
    return await run_out(session, run)


@router.patch("/{run_id}/budget", dependencies=[Depends(require_roles(MembershipRole.tenant_admin.value))])
async def budget(run_id: uuid.UUID, body: RunBudget, principal: Principal = Depends(get_current_principal), session=Depends(get_tenant_db)):
    run = await get_run(session, principal.tenant_id, run_id, lock=True)
    if run.version != body.expected_version or run.status in ("completed", "cancelled"):
        raise HTTPException(409, "Actualiza la investigación; su estado ha cambiado o ya ha terminado.")
    if not body.reason.strip(): raise HTTPException(422, "Documenta el motivo del cambio de presupuesto.")
    balance = await run_balance(session, principal.tenant_id, run_id)
    if body.budget_microusd < balance["spent_microusd"] + balance["reserved_microusd"]:
        raise HTTPException(409, "El nuevo presupuesto no puede borrar consumo o reservas existentes.")
    append_event(session, run, "budget_changed", {"before": run.budget_microusd,
        "after": body.budget_microusd, "reason": body.reason}, principal.user_id)
    run.budget_microusd = body.budget_microusd; run.version += 1
    await session.flush()
    return await run_out(session, run)
