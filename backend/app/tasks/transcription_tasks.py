"""ARQ job that transcribes a document's pages with the in-app vision OCR engines.

Orchestration (queue, progress, persistence, cancel) is the suite's; the actual transcription uses
`app/modules/transcription/ocr_engines.py` — `_ocr_via_anthropic` / `_ocr_via_openai_compat` for
vision models, and tesseract via a text-producing CLI call. Progress is published to Redis (relayed by the
SSE endpoint) and mirrored on the jobs row; each page is committed individually for durability.
The RLS GUC is transaction-local, so we re-apply it after every commit.
"""
from __future__ import annotations

import asyncio
import io
import subprocess
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..core import events, storage
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.job import Job
from ..models.transcription import Transcription
from ..modules.providers.service import ProviderService, ResolvedCredential

PAGE_TIMEOUT = 120


def _to_jpeg(img_data: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(img_data)) as im:
        if im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def _tesseract_text(img_data: bytes, lang: str, psm: int) -> str:
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang, "--psm", str(psm), "--oem", "1"],
        input=img_data,
        capture_output=True,
        timeout=PAGE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400] or "tesseract failed")
    return proc.stdout.decode("utf-8", "replace").strip()


def transcribe_image(
    img_data: bytes, rc: ResolvedCredential, *, prompt: str | None, lang: str, psm: int
) -> str:
    """Blocking OCR for one image — runs in a thread. Uses the in-app vision OCR engines."""
    from ..modules.transcription.ocr_engines import (
        DEFAULT_VISION_PROMPT, _ocr_via_anthropic, _ocr_via_openai_compat)

    if rc.engine == "tesseract":
        return _tesseract_text(img_data, lang, psm)
    jpeg = _to_jpeg(img_data)
    if rc.engine == "kraken":  # local HTR microservice (plan §6); upstream of extraction
        from ..modules.transcription.htr_kraken import htr_via_kraken

        return htr_via_kraken(jpeg, model=rc.model, base_url=rc.base_url)
    effective_prompt = prompt or DEFAULT_VISION_PROMPT
    if rc.engine == "claude":
        return _ocr_via_anthropic(jpeg, model=rc.model, api_key=rc.api_key, prompt=effective_prompt)
    return _ocr_via_openai_compat(
        jpeg, model=rc.model, api_key=rc.api_key, base_url=rc.base_url, prompt=effective_prompt
    )


async def _set_job(session, tenant_id: uuid.UUID, job_id: uuid.UUID, **values) -> None:
    await set_rls_context(session, tenant_id=tenant_id)
    await session.execute(update(Job).where(Job.id == job_id).values(**values))
    await session.commit()


async def transcribe_document(ctx, *, job_id, tenant_id, document_id, override=None, options=None):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    document_id = uuid.UUID(str(document_id))
    options = options or {}
    lang = options.get("lang", "spa")
    psm = int(options.get("psm", 6))
    prompt = options.get("prompt")
    replace = bool(options.get("replace"))  # re-recognition → new rows enter as candidates

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    async with SessionLocal() as session:
        await _set_job(
            session, tenant_id, job_id, status="running", started_at=datetime.now(timezone.utc)
        )

        await set_rls_context(session, tenant_id=tenant_id)
        try:
            rc = await ProviderService(session).resolve(
                tenant_id=tenant_id, task_type="transcription", override=override or None
            )
            doc = await session.get(Document, document_id)
            if not doc:
                raise RuntimeError("document not found")
            bucket, visibility = doc.storage_bucket, doc.visibility
            pages = (
                await session.scalars(
                    select(Page).where(Page.document_id == document_id).order_by(Page.page_no)
                )
            ).all()
            page_meta = [(p.id, p.page_no, p.storage_key) for p in pages]
            # On re-recognition, pages that already have an active transcription get the new row as a
            # candidate (is_active=false); pages without one are transcribed as active (first pass).
            active_pages: set[int] = set()
            if replace:
                active_pages = set((await session.scalars(
                    select(Transcription.page_no).where(
                        Transcription.document_id == document_id,
                        Transcription.is_active.is_(True),
                    )
                )).all())
            else:
                # Resumable: skip pages that already have a good active transcription, so a re-run
                # after an interruption (worker restart, timeout) continues instead of redoing the
                # whole book. Pages with only an 'error' row are retried.
                done_pages = set((await session.scalars(
                    select(Transcription.page_no).where(
                        Transcription.document_id == document_id,
                        Transcription.is_active.is_(True),
                        Transcription.status == "ok",
                    )
                )).all())
                page_meta = [pm for pm in page_meta if pm[1] not in done_pages]
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await _set_job(
                session, tenant_id, job_id, status="error", error=str(exc)[:1000],
                finished_at=datetime.now(timezone.utc),
            )
            await pub({"kind": "book_fail", "error": str(exc)[:400]})
            return

        total = len(page_meta)
        await pub({"kind": "book_start", "total": total, "engine": rc.engine, "model": rc.model})

        done = errors = 0
        CONCURRENCY = 6  # parallel page transcriptions per chunk (bounded for provider rate limits)

        async def _transcribe(key: str) -> str:
            img_data, _ = await storage.get_object(bucket, key)
            return await asyncio.to_thread(transcribe_image, img_data, rc, prompt=prompt, lang=lang, psm=psm)

        for start in range(0, total, CONCURRENCY):
            chunk = page_meta[start : start + CONCURRENCY]
            await set_rls_context(session, tenant_id=tenant_id)
            if await session.scalar(select(Job.status).where(Job.id == job_id)) == "cancelled":
                await pub({"kind": "log", "message": "cancelled by user"})
                return

            # fetch + transcribe the chunk's pages in parallel (no DB in the workers)
            results = await asyncio.gather(*[_transcribe(key) for (_pid, _pno, key) in chunk], return_exceptions=True)

            await set_rls_context(session, tenant_id=tenant_id)
            for (page_id, page_no, _key), res in zip(chunk, results):
                ok = not isinstance(res, Exception)
                if ok:
                    done += 1
                else:
                    errors += 1
                new_active = not (replace and page_no in active_pages)
                # When a successful new row becomes the active one (plain transcribe or first-pass of a
                # re-recognition), supersede any prior active row for this page so there's exactly one
                # active transcription per page. Only on success — a transient failure must never wipe
                # a good existing transcription (the error row enters as a candidate instead).
                if ok and new_active:
                    await session.execute(
                        update(Transcription)
                        .where(
                            Transcription.document_id == document_id,
                            Transcription.page_no == page_no,
                            Transcription.is_active.is_(True),
                        )
                        .values(is_active=False)
                    )
                session.add(Transcription(
                    tenant_id=tenant_id, document_id=document_id, page_id=page_id,
                    page_no=page_no, visibility=visibility, engine=rc.engine, model=rc.model,
                    text=res if ok else None, status="ok" if ok else "error", job_id=job_id,
                    is_active=new_active and ok,
                ))
                await pub(
                    {"kind": "page_ok", "page_no": page_no, "done": done, "total": total} if ok
                    else {"kind": "page_fail", "page_no": page_no, "error": str(res)[:300]}
                )
            await session.execute(
                update(Job).where(Job.id == job_id).values(progress={"done": done, "total": total, "errors": errors})
            )
            await session.commit()

        await _set_job(
            session, tenant_id, job_id, status="completed",
            finished_at=datetime.now(timezone.utc),
            result={"done": done, "total": total, "errors": errors},
        )
        await pub({"kind": "all_done", "done": done, "total": total, "errors": errors})
