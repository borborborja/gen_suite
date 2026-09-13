"""Write the job and its queue intent in the caller's transaction (no Redis I/O)."""
from __future__ import annotations

from fastapi import HTTPException

from ..models.task_dispatch import TaskDispatch

DISPATCH_FUNCTIONS = frozenset({
    "transcribe_document", "extract_records", "embed_document", "embed_mentions",
    "rasterize_document", "rerasterize_document", "compact_to_pdf", "export_document_archive",
    "ingest_collection", "cleanup_ingestion", "inventory_storage", "recount_storage",
    "parse_index", "generate_candidates",
    "reembed_corpus",
    "export_document_pdfa",
    "export_tree",
    "prepare_tree_import", "apply_tree_import",
})

# Fixed routing, never selected by an HTTP payload. The normal workers do not
# need the optional conversion tools and must not consume their queue.
PDFA_QUEUE = "arq:pdfa"
PDFA_HEALTH_KEY = "gensuite:pdfa:v1:health"


def queue_options(function: str) -> dict:
    return {"_queue_name": PDFA_QUEUE} if function == "export_document_pdfa" else {}


async def pdfa_available() -> bool:
    import asyncio
    from .queue import get_queue
    try:
        async with asyncio.timeout(2):
            queue = await get_queue()
            # Require an expiring heartbeat, not an accidentally permanent flag.
            return 0 < await queue.pttl(PDFA_HEALTH_KEY) <= 11_000
    except Exception:
        return False


def assert_no_secrets(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"api_key", "password", "secret", "access_token", "cookie"} and item:
                raise HTTPException(422, "Guarda la credencial en Proveedores y utiliza su identificador; no se guardan claves en tareas.")
            assert_no_secrets(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_no_secrets(item)


async def schedule(session, job, function: str, **payload) -> None:
    if function not in DISPATCH_FUNCTIONS:
        raise ValueError("Unregistered durable task")
    assert_no_secrets(payload)
    assert_no_secrets(job.params)
    await session.flush()
    session.add(TaskDispatch(
        job_id=job.id, tenant_id=job.tenant_id, function=function,
        payload={**payload, "job_id": str(job.id), "tenant_id": str(job.tenant_id)},
    ))
    await session.flush()
