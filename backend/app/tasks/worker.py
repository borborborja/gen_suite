"""ARQ worker entrypoint.  Run with:  arq app.tasks.worker.WorkerSettings"""
from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from ..settings import settings
from .document_tasks import compact_to_pdf, rasterize_document, rerasterize_document
from .maintenance_tasks import backup_db_to_s3, reap_stale_jobs
from .embedding_tasks import embed_document, embed_mentions, reembed_corpus
from .extraction_tasks import extract_records
from .fs_tasks import fs_download
from .index_tasks import parse_index
from .linkage_tasks import generate_candidates, generate_family_candidates
from .reconstruction_tasks import reconstruct_tree
from .transcription_tasks import transcribe_document


async def startup(ctx: dict) -> None:
    # Any job left 'running' was orphaned by a previous worker crash/restart (arq won't resume it),
    # so it would hang in the UI forever. Mark such zombies as error on boot. Uses the owner role
    # (bypasses RLS) since orphans span all tenants. Queued jobs stay — arq still has them.
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(settings.admin_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "UPDATE jobs SET status='error', finished_at=now(), "
                "error=COALESCE(error, 'Worker reiniciado: tarea huérfana cancelada') "
                "WHERE status='running'"
            ))
    except Exception:
        pass
    finally:
        await engine.dispose()


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [
        transcribe_document, embed_document, embed_mentions, reembed_corpus,
        extract_records, generate_candidates, rasterize_document, reconstruct_tree,
        fs_download, compact_to_pdf, parse_index, rerasterize_document, generate_family_candidates,
    ]
    # Reap stalled/orphaned jobs every 3 minutes so no section's UI hangs on a dead job (resilience);
    # daily Postgres → S3 backup (no-op unless BACKUP_TO_S3=true).
    cron_jobs = [
        cron(reap_stale_jobs, minute=set(range(0, 60, 3)), run_at_startup=True),
        cron(backup_db_to_s3, hour={settings.backup_hour}, minute={0}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 4  # cap concurrent transcription batches per worker
    # Whole-book transcription/extraction runs for many minutes; arq's default job_timeout is only
    # 300s, which would kill (and endlessly retry) any long job. Give batch jobs room. The tasks are
    # resumable (transcription skips done pages, extraction uses an anti-join), so a real hang is
    # bounded and a restart continues where it left off.
    job_timeout = 14400  # 4h
    max_tries = 2        # a genuinely failing job shouldn't retry many times
