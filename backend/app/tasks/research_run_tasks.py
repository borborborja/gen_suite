"""Short, restartable coordination ticks; no sleeping parent job occupies an ARQ slot."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ..core.dispatch import schedule
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document
from ..models.job import Job
from ..models.research_run import ResearchRun, ResearchRunStage
from ..models.task_dispatch import TaskDispatch
from ..modules.research.runs import STAGE_TASKS, append_event
from ..settings import settings


async def tick_run(session, tenant_id, run_id):
    run = await session.scalar(select(ResearchRun).where(ResearchRun.id == run_id,
        ResearchRun.tenant_id == tenant_id, ResearchRun.status == "running").with_for_update(skip_locked=True))
    if not run:
        return
    run.updated_at = datetime.now(timezone.utc)  # Fair ordering of coordinator ticks, not a job heartbeat.
    stage = await session.scalar(select(ResearchRunStage).where(ResearchRunStage.run_id == run.id,
        ResearchRunStage.status.notin_(("completed", "skipped", "cancelled"))).order_by(ResearchRunStage.position).limit(1))
    if not stage:
        run.status = "completed"; run.version += 1; run.finished_at = datetime.now(timezone.utc)
        append_event(session, run, "completed", {"meaning": "Procesamiento terminado; las propuestas requieren revisión humana."})
        return
    if stage.kind not in STAGE_TASKS:
        raise ValueError("Unknown research stage")
    job = await session.get(Job, stage.job_id) if stage.job_id else None
    if job and job.tenant_id != tenant_id:
        raise ValueError("Invalid research job tenant")
    if job and job.status == "completed":
        stage.status = "completed"; stage.result = {"job": job.result}; stage.error = None
        run.version += 1
        append_event(session, run, "stage_completed", {"stage_id": str(stage.id), "job_id": str(job.id), "kind": stage.kind})
        return  # Next tick starts the next stage; this transaction is an atomic checkpoint.
    if job and job.status in ("queued", "running"):
        return
    if job and stage.status != "paused":
        stage.status = "needs_attention"; stage.error = job.error or f"Trabajo {job.status}"
        stage.result = {"job": job.result}; run.status = "needs_attention"; run.error = stage.error; run.version += 1
        append_event(session, run, "stage_failed", {"stage_id": str(stage.id), "job_id": str(job.id), "status": job.status})
        return
    if stage.document_id:
        doc = await session.scalar(select(Document).where(Document.id == stage.document_id, Document.tenant_id == tenant_id))
        if not doc:
            stage.status = "needs_attention"; stage.error = "El documento seleccionado ya no está disponible."
            run.status = "needs_attention"; run.error = stage.error; run.version += 1
            return
    # Same PostgreSQL key as serialized_job; never requeue while an old attempt is still
    # finishing a paid call, writing output or holding its session-level advisory lock.
    job_id = stage.job_id or uuid.uuid5(stage.id, "research-stage-job")
    key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
        return
    if job:
        intent = await session.get(TaskDispatch, job.id)
        if not intent:
            raise ValueError("Missing durable research dispatch")
        intent.generation += 1; intent.status = "pending"; intent.available_at = datetime.now(timezone.utc)
        intent.error = None; intent.dispatched_at = None
        job.status = "queued"; job.error = None; job.finished_at = None
    else:
        job_type, function = STAGE_TASKS[stage.kind]
        payload = {}
        params = {"research_run_id": str(run.id), "research_stage_id": str(stage.id)}
        if stage.document_id:
            payload["document_id"] = str(stage.document_id)
            params["document_id"] = str(stage.document_id)
        if stage.person_id:
            payload.update(person_id=str(stage.person_id), max_candidates=50)
            params["person_id"] = str(stage.person_id)
        if stage.kind == "transcribe":
            payload["options"] = {"lang": run.config["lang"], "replace": False}
        elif stage.kind == "extract":
            payload["options"] = {"modality": "sync"}
        job = Job(id=job_id, tenant_id=tenant_id, type=job_type, status="queued", params=params, created_by=run.created_by)
        session.add(job)
        await schedule(session, job, function, **payload)
        stage.job_id = job.id
    stage.status = "running"; stage.attempts += 1; stage.error = None; run.version += 1
    append_event(session, run, "stage_queued", {"stage_id": str(stage.id), "job_id": str(job.id),
                                               "kind": stage.kind, "attempt": stage.attempts})


async def advance_research_runs(ctx):
    # Only enumerate IDs as maintenance/admin. Every operation uses the restricted role/RLS.
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with AsyncSession(engine) as discovery:
            targets = (await discovery.execute(select(ResearchRun.tenant_id, ResearchRun.id)
                .where(ResearchRun.status == "running").order_by(ResearchRun.updated_at, ResearchRun.id).limit(32))).all()
    finally:
        await engine.dispose()
    for tenant_id, run_id in targets:
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenant_id)
            try:
                await tick_run(session, tenant_id, run_id)
                await session.commit()  # No more queries on this transaction/session.
            except Exception:
                await session.rollback()
                await set_rls_context(session, tenant_id=tenant_id)
                run = await session.scalar(select(ResearchRun).where(ResearchRun.id == run_id,
                    ResearchRun.status == "running").with_for_update())
                if run:
                    run.status = "needs_attention"; run.version += 1
                    run.error = "No se pudo avanzar la investigación; revisa sus etapas antes de reintentar."
                    append_event(session, run, "coordinator_error", {})
                    await session.commit()
