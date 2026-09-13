"""Generation identity, current-evidence checks and durable embedding manifests."""
from __future__ import annotations
import math
import struct
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from ...models.embedding_index import EmbeddingGeneration, EmbeddingItem, EmbeddingIndexEvent
from ...models.job import Job
from ...models.research_run import ResearchRun
from ...models.tenant import Tenant
from ..providers.admission import LOCAL_ENGINES, fingerprint, snapshot, validate_local
from ..providers.service import EMBEDDING_DIM, ProviderService

RECIPE = "page-verbatim+mention-name-role-origin:v1"
MENTION_FIELDS = "jsonb_build_object('name_raw', m.name_raw, 'given', m.given, 'surname', m.surname, 'role', m.role, 'stated_origin', m.stated_origin)"
MENTION_TEXT = "btrim(concat_ws(' ', coalesce(nullif(m.name_raw,''), nullif(concat_ws(' ',nullif(m.given,''),nullif(m.surname,'')),'')), CASE WHEN nullif(m.role,'') IS NOT NULL THEN '(' || m.role || ')' END, nullif(m.stated_origin,'')))"
FROM_SQL = {
    "transcription": "transcriptions t JOIN documents d ON d.id=t.document_id",
    "mention": "person_mentions m JOIN records r ON r.id=m.record_id JOIN documents d ON d.id=r.document_id LEFT JOIN transcriptions tr ON tr.id=r.transcription_id LEFT JOIN transcriptions te ON te.id=r.transcription_end_id",
}
LIVE_SQL = {
    "transcription": "t.is_active AND nullif(btrim(t.text),'') IS NOT NULL",
    "mention": f"r.status IN ('extracted','needs_review','reviewed') AND (r.transcription_id IS NULL OR tr.is_active) AND (r.transcription_end_id IS NULL OR te.is_active) AND nullif({MENTION_TEXT},'') IS NOT NULL",
}
# Revocation is checked through joins under the source tables' RLS, never a cached visibility bit.
ITEM_JOIN = {"transcription": "t.id=e.source_id AND t.tenant_id=e.source_tenant_id AND t.document_id=e.document_id",
             "mention": "m.id=e.source_id AND m.tenant_id=e.source_tenant_id AND r.document_id=e.document_id"}
FRESH_SQL = {
    "transcription": "t.text=e.input_text AND t.page_id IS NOT DISTINCT FROM e.page_id",
    "mention": f"{MENTION_FIELDS}=e.input_fields AND r.id=e.record_id AND r.page_id IS NOT DISTINCT FROM e.page_id AND r.transcription_id IS NOT DISTINCT FROM e.transcription_id AND r.transcription_end_id IS NOT DISTINCT FROM e.transcription_end_id",
}


def public_profile(rc):
    if rc.engine not in {"openai", "ollama", "jina"}:
        raise HTTPException(409, "El perfil elegido no admite embeddings.")
    if rc.engine in LOCAL_ENGINES:
        validate_local(rc)
        return dict(engine=rc.engine, model=rc.model, base_url=rc.base_url,
                    credential_id=str(rc.credential_id) if rc.credential_id else None)
    return snapshot(rc)


def space_key(profile):
    # A different tariff/key is not a different mathematical space; endpoint/model/recipe are.
    return fingerprint({key: profile.get(key) for key in ("engine", "model", "base_url")} |
                       {"dimensions": EMBEDDING_DIM, "recipe": RECIPE})


def validate_vectors(vectors, count):
    if len(vectors) != count:
        raise ValueError("El proveedor devolvió un lote incompleto de vectores")
    for vector in vectors:
        if len(vector) != EMBEDDING_DIM or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
            raise ValueError("Vector de dimensión incorrecta o con valores no finitos")
        try:
            stored = [struct.unpack("f", struct.pack("f", value))[0] for value in vector]
        except (OverflowError, struct.error):
            raise ValueError("Vector fuera del rango de almacenamiento float32") from None
        if not all(math.isfinite(value) for value in stored):
            raise ValueError("Vector fuera del rango de almacenamiento float32")
        if not any(x != 0 for x in stored):
            raise ValueError("Un vector nulo no permite calcular similitud coseno")


def event(session, generation, kind, detail, actor=None):
    session.add(EmbeddingIndexEvent(tenant_id=generation.tenant_id, generation_id=generation.id,
        kind=kind, detail=detail, actor_id=actor))


async def get_generation(session, tenant_id, generation_id):
    generation = await session.scalar(select(EmbeddingGeneration).where(
        EmbeddingGeneration.id == generation_id, EmbeddingGeneration.tenant_id == tenant_id))
    if not generation: raise HTTPException(404, "Generación de búsqueda no encontrada.")
    return generation


async def active_generation(session, tenant_id):
    return await session.scalar(select(EmbeddingGeneration).where(
        EmbeddingGeneration.tenant_id == tenant_id, EmbeddingGeneration.is_active.is_(True)))


async def query_context(session, tenant_id):
    """The query uses the index's model, never an unrelated newly selected provider."""
    from ...core.execution_context import current_job
    context = current_job.get()
    job_id = context[1] if context and context[0] == tenant_id else None
    job = await session.get(Job, job_id) if job_id else None
    run_id = (job.params or {}).get("research_run_id") if job else None
    if run_id:
        run = await session.get(ResearchRun, uuid.UUID(run_id))
        generation_id = (run.config or {}).get("index_generation_id") if run else None
        generation = await get_generation(session, tenant_id, uuid.UUID(generation_id)) if generation_id else await active_generation(session, tenant_id)
        if not generation or generation.status != "ready":
            raise HTTPException(409, "La investigación necesita terminar una generación de búsqueda antes de comparar personas.")
        rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="embedding", job_id=job_id)
        if space_key(public_profile(rc)) != generation.space_key:
            raise HTTPException(409, "El modelo de esta investigación no coincide con el índice disponible. Genera su índice antes de buscar.")
    else:
        generation = await active_generation(session, tenant_id)
        if not generation:
            raise HTTPException(409, "No hay índice vectorial activo con modelo acreditado. Crea y activa una generación en Ajustes; la búsqueda por palabras sigue disponible.")
        rc = await ProviderService(session).resolve_frozen(tenant_id=tenant_id, task_type="embedding", profile=generation.profile,
            job_id=job_id, embedding_generation_id=generation.id)
    return generation, rc


async def generation_for_job(session, tenant_id, job, rc):
    profile = public_profile(rc)
    # Serialize generation selection/creation. Release before any provider call.
    await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
    gid = (job.params or {}).get("index_generation_id")
    run = None
    if gid:
        generation = await get_generation(session, tenant_id, uuid.UUID(gid))
    else:
        if rc.research_run_id:
            run = await session.scalar(select(ResearchRun).where(ResearchRun.id == rc.research_run_id,
                ResearchRun.tenant_id == tenant_id).with_for_update())
            if not run: raise HTTPException(404, "Investigación no encontrada.")
            gid = uuid.uuid5(run.id, "embedding-generation:v1")
            generation = await session.get(EmbeddingGeneration, gid)
        else:
            generation = await active_generation(session, tenant_id)
            # A retry must keep the original admission fingerprint as well as vector space.
            # Changed tariffs/credentials create a separate generation, never a half-frozen job.
            if generation and (generation.space_key != space_key(profile) or generation.profile != profile): generation = None
            gid = uuid.uuid5(job.id, "embedding-generation:v1")
        if not generation:
            generation = EmbeddingGeneration(id=gid, tenant_id=tenant_id, title=run.title if run else "Índice incremental",
                purpose="research" if run else "incremental", status="building", is_active=False, include_public=False,
                version=1, profile=profile, space_key=space_key(profile), job_id=job.id,
                research_run_id=run.id if run else None, created_by=job.created_by)
            session.add(generation); await session.flush()
            event(session, generation, "created", {"purpose": generation.purpose})
        job.params = {**(job.params or {}), "index_generation_id": str(generation.id)}
        if run:
            run.config = {**run.config, "index_generation_id": str(generation.id)}
            run.version += 1
    generation = await session.scalar(select(EmbeddingGeneration).where(EmbeddingGeneration.id == generation.id)
        .with_for_update().execution_options(populate_existing=True))
    if generation.status == "cancelled" or generation.space_key != space_key(profile):
        raise HTTPException(409, "El trabajo no corresponde al perfil o estado de esta generación.")
    return generation


async def capture_manifest(session, generation, job, kind, document_id=None, *, limit=128):
    """Capture a bounded source page; cursor and item rows commit together with the job."""
    key = f"index_manifest_{kind}"
    checkpoint = (job.params or {}).get(key, {})
    if checkpoint.get("done"): return False
    alias = "t" if kind == "transcription" else "m"
    doc = "t" if kind == "transcription" else "r"
    if kind == "transcription":
        projection = "t.id AS source_id,t.tenant_id AS source_tenant_id,t.document_id,t.page_id,t.id AS transcription_id,NULL::uuid AS transcription_end_id,NULL::uuid AS record_id,t.text AS input_text,'{}'::jsonb AS input_fields"
    else:
        projection = f"m.id AS source_id,m.tenant_id AS source_tenant_id,r.document_id,r.page_id,r.transcription_id,r.transcription_end_id,r.id AS record_id,{MENTION_TEXT} AS input_text,{MENTION_FIELDS} AS input_fields"
    params = dict(tid=generation.tenant_id, public=generation.include_public, limit=limit,
                  after=uuid.UUID(checkpoint["after"]) if checkpoint.get("after") else uuid.UUID(int=0))
    condition = f"{alias}.id > :after AND ({alias}.tenant_id=:tid OR (:public AND {alias}.visibility='public' AND d.visibility='public'))"
    if document_id:
        condition += f" AND {doc}.document_id=:doc"; params["doc"] = document_id
    rows = (await session.execute(text(f"SELECT {projection} FROM {FROM_SQL[kind]} WHERE {LIVE_SQL[kind]} AND {condition} ORDER BY {alias}.id LIMIT :limit"), params)).mappings().all()
    values = []
    for row in rows:
        data = dict(row)
        source_hash = fingerprint({k: str(v) if isinstance(v, uuid.UUID) else v for k, v in data.items()})
        values.append(dict(**data, id=uuid.uuid5(generation.id, f"{kind}:{row['source_id']}:{source_hash}"),
            tenant_id=generation.tenant_id, generation_id=generation.id, kind=kind, source_hash=source_hash,
            job_id=job.id, status="pending"))
    if values:
        await session.execute(insert(EmbeddingItem).values(values).on_conflict_do_nothing(constraint="uq_embedding_item_source"))
    job.params = {**(job.params or {}), key: {"done": len(rows) < limit,
        "after": str(rows[-1]["source_id"]) if rows else checkpoint.get("after")}}
    return bool(rows)


async def current_item_ids(session, generation_id, kind, ids=None):
    params = {"gid": generation_id}
    extra = ""
    if ids is not None:
        if not ids: return set()
        extra = " AND e.id = ANY(:ids)"; params["ids"] = list(ids)
    rows = await session.scalars(text(f"SELECT e.id FROM {FROM_SQL[kind]} JOIN embedding_items e ON {ITEM_JOIN[kind]} WHERE e.generation_id=:gid AND e.kind='{kind}' AND {LIVE_SQL[kind]} AND {FRESH_SQL[kind]}{extra}"), params)
    return set(rows.all())


async def coverage(session, generation):
    counts = dict((await session.execute(select(EmbeddingItem.status, func.count()).where(
        EmbeddingItem.generation_id == generation.id).group_by(EmbeddingItem.status))).all())
    valid = {}
    for kind in ("transcription", "mention"):
        valid[kind] = int(await session.scalar(text(f"SELECT count(*) FROM {FROM_SQL[kind]} JOIN embedding_items e ON {ITEM_JOIN[kind]} WHERE e.generation_id=:gid AND e.kind='{kind}' AND e.status='ready' AND {LIVE_SQL[kind]} AND {FRESH_SQL[kind]}"), {"gid": generation.id}) or 0)
    return {"counts": counts, "searchable": valid, "outdated": counts.get("stale", 0) + counts.get("ready", 0) - sum(valid.values())}


async def generation_out(session, generation):
    return {"id": generation.id, "title": generation.title, "purpose": generation.purpose,
        "status": generation.status, "is_active": generation.is_active, "version": generation.version,
        "include_public": generation.include_public, "profile": generation.profile,
        "space_key": generation.space_key, "job_id": generation.job_id, "research_run_id": generation.research_run_id,
        "error": generation.error, "created_at": generation.created_at, **await coverage(session, generation)}
