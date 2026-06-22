"""ARQ job: super-discovery — reconstruct a proposed family tree from the corpus."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update

from ..core import events
from ..db.rls import commit_keep_rls, set_rls_context
from ..db.session import SessionLocal
from ..models.job import Job
from ..models.reconstruction import Reconstruction
from ..modules.linkage import reconstruct as recon


async def reconstruct_tree(
    ctx, *, job_id, tenant_id, reconstruction_id, conservative=True, include_census=False, link_to_tree=True,
):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    reconstruction_id = uuid.UUID(str(reconstruction_id))

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="running", started_at=datetime.now(timezone.utc)))
        await session.commit()
        await pub({"kind": "book_start"})

        async def on_progress(ev: dict) -> None:
            await pub({"kind": "page_ok", "done": ev.get("done", 0), "total": ev.get("total", 0),
                       "phase": ev.get("phase")})
            await session.execute(update(Job).where(Job.id == job_id).values(
                progress={"done": ev.get("done", 0), "total": ev.get("total", 0), "phase": ev.get("phase")}))
            # commit_keep_rls, not bare commit: the GUCs are transaction-local, so a plain commit here
            # would drop them and the FINAL status update below would silently match 0 rows under RLS.
            await commit_keep_rls(session, tenant_id=tenant_id)

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            graph, stats = await recon.build_reconstruction(
                session, tenant_id, conservative=conservative, include_census=include_census,
                link_to_tree=link_to_tree, on_progress=on_progress)
            await set_rls_context(session, tenant_id=tenant_id)  # build may have committed via on_progress
            await session.execute(update(Reconstruction).where(Reconstruction.id == reconstruction_id).values(
                graph=graph, stats=stats, status="completed"))
            await session.execute(update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc), result=stats))
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(update(Reconstruction).where(Reconstruction.id == reconstruction_id).values(status="error"))
            await session.execute(update(Job).where(Job.id == job_id).values(
                status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)))
            await session.commit()
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return

        await pub({"kind": "all_done", **stats})
