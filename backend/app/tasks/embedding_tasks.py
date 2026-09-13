"""Bounded, resumable generation builds. Never erase legacy or active corpus vectors."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func, select, update
from ..core import events
from ..core.job_lifecycle import job_is_active, update_active_job
from ..core.task_execution import serialized_job
from ..db.rls import set_rls_context, commit_keep_rls
from ..db.session import SessionLocal
from ..models.job import Job
from ..models.embedding_index import EmbeddingItem, EmbeddingGeneration
from ..models.research_run import ResearchRunStage
from ..modules.providers.service import ProviderService, embed_texts_with_usage
from ..modules.search import indexes

BATCH = 16


def embedding_chunks(rc, targets):
    from ..modules.providers.admission import LOCAL_ENGINES, policy_for
    limit = None if rc.engine in LOCAL_ENGINES else policy_for(rc, fresh=False).context_tokens
    chunk, size = [], 0
    for row in targets:
        row_size = len(row[1].encode("utf-8")) + 32
        if chunk and (len(chunk) == BATCH or (limit is not None and size + row_size > limit)):
            yield chunk; chunk, size = [], 0
        chunk.append(row); size += row_size
    if chunk: yield chunk


async def _publish(tenant_id, job_id, event):
    try: await events.publish(tenant_id, job_id, event)
    except Exception: pass  # Results and accounting must survive pub/sub outages.


async def _start(session, tenant_id, job_id):
    changed = await update_active_job(session, job_id, status="running", started_at=datetime.now(timezone.utc))
    await commit_keep_rls(session, tenant_id=tenant_id)
    return changed


async def _finish(session, tenant_id, job_id, *, result=None, error=None):
    changed = await update_active_job(session, job_id, status="error" if error else "completed",
        result=result, error=error, finished_at=datetime.now(timezone.utc))
    await commit_keep_rls(session, tenant_id=tenant_id)
    await _publish(tenant_id, job_id, {"kind": ("book_fail" if error else "all_done") if changed else "cancelled",
        "error": error, **(result or {})})


async def _build(ctx, *, job_id, tenant_id, kinds, document_id=None):
    jid, tid = uuid.UUID(str(job_id)), uuid.UUID(str(tenant_id))
    did = uuid.UUID(str(document_id)) if document_id else None
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        if not await _start(session, tid, jid): return
        generation_id = None
        try:
            rc = await ProviderService(session).resolve(tenant_id=tid, task_type="embedding", job_id=jid)
            job = await session.get(Job, jid)
            generation = await indexes.generation_for_job(session, tid, job, rc)
            generation_id = generation.id
            rc.embedding_generation_id = generation.id
            if not generation.is_active: generation.status = "building"
            generation.error = None
            await commit_keep_rls(session, tenant_id=tid)
            for kind in kinds:
                while not (job.params or {}).get(f"index_manifest_{kind}", {}).get("done"):
                    if not await job_is_active(session, jid): return
                    await indexes.capture_manifest(session, generation, job, kind, did)
                    await session.execute(update(Job).where(Job.id == jid).values(progress={"phase": "manifest", "kind": kind}))
                    await commit_keep_rls(session, tenant_id=tid)
                while True:
                    if not await job_is_active(session, jid): return
                    items = list((await session.scalars(select(EmbeddingItem).where(
                        EmbeddingItem.generation_id == generation.id, EmbeddingItem.job_id == jid,
                        EmbeddingItem.kind == kind, EmbeddingItem.status == "pending"
                    ).order_by(EmbeddingItem.id).limit(128))).all())
                    if not items: break
                    valid = await indexes.current_item_ids(session, generation.id, kind, [item.id for item in items])
                    for item in items:
                        if item.id not in valid: item.status = "stale"
                    await commit_keep_rls(session, tenant_id=tid)
                    for chunk in embedding_chunks(rc, [(item.id, item.input_text) for item in items if item.id in valid]):
                        if not await job_is_active(session, jid): return
                        vectors, usage = await embed_texts_with_usage(rc, [value for _, value in chunk])
                        indexes.validate_vectors(vectors, len(chunk))
                        # Keep an admitted response even if evidence changes in flight.
                        # Retrieval rechecks the exact input/revision.
                        for (item_id, _), vector in zip(chunk, vectors):
                            await session.execute(update(EmbeddingItem).where(EmbeddingItem.id == item_id,
                                EmbeddingItem.tenant_id == tid, EmbeddingItem.status == "pending").values(
                                embedding=vector, status="ready", ai_call_id=uuid.UUID(usage["ai_call_id"]) if usage.get("ai_call_id") else None))
                        done = int(await session.scalar(select(func.count()).select_from(EmbeddingItem).where(
                            EmbeddingItem.job_id == jid, EmbeddingItem.status == "ready")) or 0)
                        await session.execute(update(Job).where(Job.id == jid).values(progress={"phase": "embedding", "done": done}))
                        await commit_keep_rls(session, tenant_id=tid)
                        await _publish(tid, jid, {"kind": "page_ok", "done": done})
            other_pending = select(EmbeddingItem.id).where(EmbeddingItem.generation_id == generation.id,
                EmbeddingItem.status == "pending", EmbeddingItem.kind.in_(kinds), EmbeddingItem.job_id != jid)
            if did: other_pending = other_pending.where(EmbeddingItem.document_id == did)
            if await session.scalar(other_pending.limit(1)):
                raise HTTPException(409, "Hay una indexación anterior pendiente sobre estas fuentes. Reanuda su trabajo o construye una generación completa nueva desde Ajustes.")
            generation = await session.scalar(select(EmbeddingGeneration).where(EmbeddingGeneration.id == generation.id)
                .with_for_update().execution_options(populate_existing=True))
            if not await job_is_active(session, jid) or generation.status == "cancelled": return
            remaining = 0
            if generation.research_run_id:
                remaining = int(await session.scalar(select(func.count()).select_from(ResearchRunStage).where(
                    ResearchRunStage.run_id == generation.research_run_id,
                    ResearchRunStage.kind.in_(("embed_text", "embed_mentions", "embed_corpus")),
                    ResearchRunStage.status.notin_(("completed", "skipped", "cancelled")),
                    ResearchRunStage.job_id.is_distinct_from(jid))) or 0)
            if not remaining: generation.status = "ready"
            generation.version += 1
            counts = dict((await session.execute(select(EmbeddingItem.kind, func.count()).where(
                EmbeddingItem.job_id == jid, EmbeddingItem.status == "ready").group_by(EmbeddingItem.kind))).all())
            indexes.event(session, generation, "job_completed", {"job_id": str(jid), "counts": counts})
            await _finish(session, tid, jid, result={"generation_id": str(generation.id),
                "transcriptions": counts.get("transcription", 0), "mentions": counts.get("mention", 0),
                "model": rc.model, "activation_required": not generation.is_active})
        except Exception as exc:
            await session.rollback(); await set_rls_context(session, tenant_id=tid)
            error = str(getattr(exc, "detail", "No se pudo completar el índice; se conservan los vectores y las reservas."))[:1000]
            if generation_id:
                generation = await session.scalar(select(EmbeddingGeneration).where(EmbeddingGeneration.id == generation_id)
                    .with_for_update().execution_options(populate_existing=True))
                if generation and not generation.is_active and generation.status != "cancelled":
                    generation.status = "error"; generation.error = error; generation.version += 1
            await _finish(session, tid, jid, error=error)


@serialized_job
async def embed_document(ctx, *, job_id, tenant_id, document_id):
    return await _build(ctx, job_id=job_id, tenant_id=tenant_id, document_id=document_id, kinds=("transcription",))


@serialized_job
async def embed_mentions(ctx, *, job_id, tenant_id, document_id):
    return await _build(ctx, job_id=job_id, tenant_id=tenant_id, document_id=document_id, kinds=("mention",))


@serialized_job
async def reembed_corpus(ctx, *, job_id, tenant_id):
    return await _build(ctx, job_id=job_id, tenant_id=tenant_id, kinds=("transcription", "mention"))
