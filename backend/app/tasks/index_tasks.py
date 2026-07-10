"""ARQ job: parse a register's INDEX pages (name→folio) into index_entries, then cross-check them
against the extracted Records to flag acts the extraction missed or mis-read."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from ..core import events
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.index_entry import IndexEntry
from ..models.job import Job
from ..models.mention import PersonMention
from ..models.record import Record
from ..models.transcription import Transcription
from ..modules.extraction.normalize import compute_keys, split_name
from ..modules.extraction.schemas import INDEX_SYSTEM_PROMPT, IndexPage
from ..modules.providers.service import ProviderService, extract_structured_with_usage, record_usage


async def cross_check_index(session, *, tenant_id, document_id) -> dict:
    """Mark each index entry matched/unmatched against the book's extracted records, by entry number
    or by folio + surname. Returns a summary. The 'indexed book' is this document, or — if it is a
    standalone index — the document it indexes (indexes_for_id)."""
    doc = await session.get(Document, document_id)
    book_id = doc.indexes_for_id if (doc and doc.is_index and doc.indexes_for_id) else document_id

    records = (await session.scalars(
        select(Record).where(Record.document_id == book_id, Record.status != "superseded")
    )).all()
    pid_to_folio = dict((await session.execute(
        select(Page.id, Page.folio_label).where(Page.document_id == book_id)
    )).all())
    by_record_no = {r.record_no for r in records if r.record_no}
    # folio → set of normalised surnames present in records on that folio
    folio_surnames: dict[str, set[str]] = {}
    rec_ids = [r.id for r in records]
    if rec_ids:
        ments = (await session.scalars(
            select(PersonMention).where(PersonMention.record_id.in_(rec_ids))
        )).all()
        rec_folio = {r.id: pid_to_folio.get(r.page_id) for r in records}
        for m in ments:
            fol = rec_folio.get(m.record_id)
            if fol and m.norm_surname:
                folio_surnames.setdefault(fol, set()).add(m.norm_surname)

    entries = (await session.scalars(
        select(IndexEntry).where(IndexEntry.document_id == document_id)
    )).all()
    matched = 0
    for e in entries:
        ok = False
        if e.record_no and e.record_no in by_record_no:
            ok = True
        elif e.folio_label and e.norm_surname and e.norm_surname in folio_surnames.get(e.folio_label, set()):
            ok = True
        elif e.folio_label and e.folio_label in pid_to_folio.values():
            # folio exists and has records but the surname didn't line up — count as a weak match
            ok = e.norm_surname is None
        e.matched = ok
        matched += 1 if ok else 0
    await session.flush()
    return {"index_entries": len(entries), "matched": matched, "missing": len(entries) - matched}


async def parse_index(ctx, *, job_id, tenant_id, document_id):
    job_id = uuid.UUID(str(job_id)); tenant_id = uuid.UUID(str(tenant_id)); document_id = uuid.UUID(str(document_id))

    async def pub(ev: dict) -> None:
        await events.publish(tenant_id, job_id, ev)

    schema = IndexPage.model_json_schema()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="running", started_at=datetime.now(timezone.utc)))
        await session.commit()
        try:
            rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="inference")
        except Exception as exc:
            await set_rls_context(session, tenant_id=tenant_id)
            await session.execute(update(Job).where(Job.id == job_id).values(
                status="error", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc)))
            await session.commit(); await pub({"kind": "book_fail", "error": str(exc)[:200]}); return

        doc = await session.get(Document, document_id)
        # Index pages: explicitly-tagged 'index' pages, or every page if the whole doc is an index.
        page_filter = [Transcription.document_id == document_id, Transcription.is_active.is_(True),
                       Transcription.text.is_not(None)]
        if not (doc and doc.is_index):
            idx_pages = select(Page.id).where(Page.document_id == document_id, Page.kind == "index")
            page_filter.append(Transcription.page_id.in_(idx_pages))
        rows = (await session.scalars(select(Transcription).where(*page_filter))).all()
        targets = [(t.id, t.page_id, t.text) for t in rows]
        # fresh parse: drop prior index entries for this doc
        await session.execute(delete(IndexEntry).where(IndexEntry.document_id == document_id))
        await session.commit()
        await pub({"kind": "book_start", "total": len(targets)})

        tk = {"prompt": 0, "completion": 0}
        done = made = 0
        for start in range(0, len(targets), 6):
            chunk = targets[start:start + 6]
            results = await asyncio.gather(*[
                asyncio.to_thread(extract_structured_with_usage, rc, t, schema=schema,
                                  system=INDEX_SYSTEM_PROMPT, schema_name="IndexPage")
                for (_id, _pid, t) in chunk], return_exceptions=True)
            await set_rls_context(session, tenant_id=tenant_id)
            for (_tid, page_id, _t), res in zip(chunk, results):
                done += 1
                if isinstance(res, Exception):
                    continue
                raw, usage = res
                tk["prompt"] += usage.get("prompt", 0); tk["completion"] += usage.get("completion", 0)
                try:
                    pg = IndexPage.model_validate(raw)
                except Exception:
                    continue
                for ent in pg.entries:
                    given = ent.given or split_name(ent.name_raw)[0] or None
                    surname = ent.surname or split_name(ent.name_raw)[1] or None
                    keys = compute_keys(given, surname)
                    session.add(IndexEntry(
                        tenant_id=tenant_id, document_id=document_id, page_id=page_id,
                        name_raw=ent.name_raw, given=given, surname=surname,
                        norm_surname=keys.get("norm_surname"), folio_label=ent.folio_label,
                        record_no=ent.record_no, year=ent.year, record_type=ent.record_type,
                        raw_json=ent.model_dump(),
                    ))
                    made += 1
            await session.execute(update(Job).where(Job.id == job_id).values(
                progress={"done": done, "total": len(targets), "entries": made}))
            await session.commit()
            await pub({"kind": "page_ok", "done": done, "total": len(targets), "entries": made})

        await set_rls_context(session, tenant_id=tenant_id)
        check = await cross_check_index(session, tenant_id=tenant_id, document_id=document_id)
        await record_usage(session, tenant_id=tenant_id, job_id=job_id, task_type="index",
                           model=rc.model, prompt_tokens=tk["prompt"], completion_tokens=tk["completion"])
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="completed", finished_at=datetime.now(timezone.utc),
            result={"index_pages": done, "entries": made, **check}))
        await session.commit()
        await pub({"kind": "all_done", "entries": made, **check})
