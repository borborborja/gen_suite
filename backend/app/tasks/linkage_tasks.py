"""ARQ job: discover candidate corpus matches for one tree Person (plan §4). Wraps
``linkage.service.generate_candidates`` with the standard job lifecycle + RLS handling."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update

from ..core import events
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.job import Job
from ..modules.linkage import service as linkage_service


async def generate_candidates(ctx, *, job_id, tenant_id, person_id, max_candidates=50):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    person_id = uuid.UUID(str(person_id))

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

        await pub({"kind": "book_start"})

        async def on_progress(ev: dict) -> None:
            # Map linkage phases to the generic done/total the UI reads — both via SSE (live stream)
            # and persisted on the Job row (so the Inicio "actividad reciente" polling shows it too).
            done, total = ev.get("done", 0), ev.get("total", 0)
            await pub({"kind": "page_ok", "done": done, "total": total, "phase": ev.get("phase")})
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    progress={"done": done, "total": total, "phase": ev.get("phase")})
            )
            # commit_keep_rls, not bare commit: the GUCs are transaction-local, so a plain commit
            # here would drop them and every tenant-scoped query that follows in
            # linkage_service.generate_candidates would silently see zero rows.
            await commit_keep_rls(session, tenant_id=tenant_id)

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            count = await linkage_service.generate_candidates(
                session, tenant_id, person_id, max_candidates, on_progress=on_progress
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc),
                result={"candidates": count},
            )
        )
        await session.commit()
        await pub({"kind": "all_done", "candidates": count})


async def generate_family_candidates(ctx, *, job_id, tenant_id, person_id, max_candidates=50):
    """Discover the SIBLING SET of a tree person (other baptisms with the same parents) → siblings +
    the parents they confirm. Same job lifecycle as ``generate_candidates``."""
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    person_id = uuid.UUID(str(person_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="running", started_at=datetime.now(timezone.utc)))
        await session.commit()
        await pub({"kind": "book_start"})

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            count = await linkage_service.generate_family_candidates(
                session, tenant_id, person_id, max_candidates)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(update(Job).where(Job.id == job_id).values(
                status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)))
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="completed", finished_at=datetime.now(timezone.utc),
            result={"candidates": count, "kind": "family"}))
        await session.commit()
        await pub({"kind": "all_done", "candidates": count})
