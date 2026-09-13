"""ARQ worker entrypoint.  Run with:  arq app.tasks.worker.WorkerSettings"""
from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from ..settings import settings
from .document_tasks import compact_to_pdf, rasterize_document, rerasterize_document, export_document_archive, export_document_pdfa
from .dispatch_tasks import dispatch_pending_tasks
from .maintenance_tasks import backup_db_to_s3, reap_stale_jobs
from .embedding_tasks import embed_document, embed_mentions, reembed_corpus
from .extraction_tasks import extract_records
from .fs_tasks import fs_download
from .index_tasks import parse_index
from .ingestion_tasks import ingest_collection
from .ingest_cleanup_tasks import cleanup_ingestion
from .storage_inventory_tasks import inventory_storage
from .storage_quota_tasks import recount_storage
from .research_run_tasks import advance_research_runs
from .linkage_tasks import generate_candidates, generate_family_candidates
from .reconstruction_tasks import reconstruct_tree
from .transcription_tasks import transcribe_document
from .tree_export_tasks import export_tree
from .tree_import_tasks import prepare_tree_import, apply_tree_import


async def startup(ctx: dict) -> None:
    # A worker can be restarted while another worker is healthy.  Never mark *all* running
    # jobs as failed here: their heartbeat/reaper remains the authority for stale work.
    # This also avoids falsely telling a researcher that a long batch was cancelled.
    return None


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [
        transcribe_document, embed_document, embed_mentions, reembed_corpus,
        extract_records, generate_candidates, rasterize_document, reconstruct_tree,
        fs_download, compact_to_pdf, parse_index, rerasterize_document, generate_family_candidates,
        export_document_archive,
        # Registered for explicit/manual execution too; durable dispatch routes
        # this function ONLY to PdfaWorkerSettings.queue_name below.
        export_document_pdfa,
        ingest_collection,
        cleanup_ingestion,
        inventory_storage,
        recount_storage,
        export_tree,
        prepare_tree_import, apply_tree_import,
    ]
    # Reap stalled/orphaned jobs every 3 minutes so no section's UI hangs on a dead job (resilience);
    # daily Postgres → S3 backup (no-op unless BACKUP_TO_S3=true).
    cron_jobs = [
        cron(advance_research_runs, second={5, 20, 35, 50}, run_at_startup=True),
        cron(dispatch_pending_tasks, second={0, 15, 30, 45}, run_at_startup=True),
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


async def pdfa_startup(ctx: dict) -> None:
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from ..modules.documents.pdfa import check_tools
    with TemporaryDirectory(prefix="gensuite-pdfa-startup-") as tmp:
        await check_tools(Path(tmp))


class PdfaWorkerSettings:
    """Optional worker: no general jobs, paid calls, backups or admin-DB cron."""
    from ..core.dispatch import PDFA_QUEUE as queue_name, PDFA_HEALTH_KEY as health_check_key
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [export_document_pdfa]
    on_startup = pdfa_startup
    max_jobs = 1
    job_timeout = 21600
    max_tries = 1
    health_check_interval = 10
