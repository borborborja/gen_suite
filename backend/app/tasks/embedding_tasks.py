"""ARQ job: embed a document's transcriptions for semantic search (Phase 5)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..core import events
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.job import Job
from ..models.mention import PersonMention
from ..models.record import Record
from ..models.transcription import Transcription
from ..modules.providers.service import ProviderService, embed_texts

BATCH = 16


def _mention_text(m: PersonMention) -> str:
    """Synthesized string embedded per mention (plan §2): name + role + stated origin."""
    parts = [m.name_raw or " ".join(filter(None, [m.given, m.surname]))]
    if m.role:
        parts.append(f"({m.role})")
    if m.stated_origin:
        parts.append(m.stated_origin)
    return " ".join(p for p in parts if p).strip()


async def embed_document(ctx, *, job_id, tenant_id, document_id):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    document_id = uuid.UUID(str(document_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="running", started_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="embedding")
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        rows = (
            await session.scalars(
                select(Transcription).where(
                    Transcription.document_id == document_id,
                    Transcription.text.is_not(None),
                    Transcription.embedding.is_(None),
                    Transcription.is_active.is_(True),
                )
            )
        ).all()
        targets = [(r.id, r.text) for r in rows]
        await session.commit()

        total = len(targets)
        done = 0
        await pub({"kind": "book_start", "total": total, "engine": rc.engine})
        for i in range(0, total, BATCH):
            chunk = targets[i : i + BATCH]
            vectors = await asyncio.to_thread(embed_texts, rc, [t for _, t in chunk])
            await set_rls_context(session, tenant_id=tenant_id)
            for (tid, _), vec in zip(chunk, vectors):
                await session.execute(
                    update(Transcription).where(Transcription.id == tid).values(embedding=vec)
                )
            done += len(chunk)
            await session.execute(
                update(Job).where(Job.id == job_id).values(progress={"done": done, "total": total})
            )
            await session.commit()
            await pub({"kind": "page_ok", "done": done, "total": total})

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc),
                result={"embedded": done, "total": total},
            )
        )
        await session.commit()
        await pub({"kind": "all_done", "embedded": done, "total": total})


async def reembed_corpus(ctx, *, job_id, tenant_id):
    """Re-embed the WHOLE corpus (all transcriptions + all mentions) for a tenant after switching
    the embedding model. Vectors from different models live in different spaces, so this first
    NULLs every embedding, then recomputes with the now-active provider. One job to poll from the
    settings UI's "re-embed" button."""
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="running", started_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="embedding")
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        # wipe existing vectors (they belong to the old model's space)
        await session.execute(update(Transcription).values(embedding=None))
        await session.execute(update(PersonMention).values(embedding=None))
        await session.commit()

        # transcriptions
        await set_rls_context(session, tenant_id=tenant_id)
        trows = (await session.scalars(select(Transcription).where(Transcription.text.is_not(None)))).all()
        ttargets = [(r.id, r.text) for r in trows]
        # mentions
        mrows = (await session.scalars(select(PersonMention))).all()
        mtargets = [(m.id, _mention_text(m)) for m in mrows]
        mtargets = [(mid, t) for mid, t in mtargets if t]
        await session.commit()

        total = len(ttargets) + len(mtargets)
        done = 0
        await pub({"kind": "book_start", "total": total, "engine": rc.engine})

        for label, targets, model in (("tx", ttargets, Transcription), ("mn", mtargets, PersonMention)):
            for i in range(0, len(targets), BATCH):
                chunk = targets[i : i + BATCH]
                vectors = await asyncio.to_thread(embed_texts, rc, [t for _, t in chunk])
                await set_rls_context(session, tenant_id=tenant_id)
                for (rid, _), vec in zip(chunk, vectors):
                    await session.execute(update(model).where(model.id == rid).values(embedding=vec))
                done += len(chunk)
                await session.execute(
                    update(Job).where(Job.id == job_id).values(progress={"done": done, "total": total})
                )
                await session.commit()
                await pub({"kind": "page_ok", "done": done, "total": total})

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc),
                result={"transcriptions": len(ttargets), "mentions": len(mtargets), "model": rc.model},
            )
        )
        await session.commit()
        await pub({"kind": "all_done", "total": total, "model": rc.model})


async def embed_mentions(ctx, *, job_id, tenant_id, document_id):
    """Embed a document's PersonMentions for hybrid retrieval (plan §2). Mirrors ``embed_document``
    but over mentions, embedding the synthesized name+role+origin string. Resumable: skips mentions
    that already have an embedding."""
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    document_id = uuid.UUID(str(document_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="running", started_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="embedding")
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        # mentions of this document's records that aren't embedded yet
        rows = (
            await session.scalars(
                select(PersonMention)
                .join(Record, Record.id == PersonMention.record_id)
                .where(Record.document_id == document_id, PersonMention.embedding.is_(None))
            )
        ).all()
        targets = [(m.id, _mention_text(m)) for m in rows]
        targets = [(mid, t) for mid, t in targets if t]
        await session.commit()

        total = len(targets)
        done = 0
        await pub({"kind": "book_start", "total": total, "engine": rc.engine})
        for i in range(0, total, BATCH):
            chunk = targets[i : i + BATCH]
            vectors = await asyncio.to_thread(embed_texts, rc, [t for _, t in chunk])
            await set_rls_context(session, tenant_id=tenant_id)
            for (mid, _), vec in zip(chunk, vectors):
                await session.execute(
                    update(PersonMention).where(PersonMention.id == mid).values(embedding=vec)
                )
            done += len(chunk)
            await session.execute(
                update(Job).where(Job.id == job_id).values(progress={"done": done, "total": total})
            )
            await session.commit()
            await pub({"kind": "page_ok", "done": done, "total": total})

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc),
                result={"embedded": done, "total": total},
            )
        )
        await session.commit()
        await pub({"kind": "all_done", "embedded": done, "total": total})
