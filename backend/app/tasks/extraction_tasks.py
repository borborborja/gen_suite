"""ARQ job: extract structured records + person mentions from a document's transcriptions
(plan §2). Structural clone of embedding_tasks: resolve the ``inference`` provider, select the
document's transcriptions that don't yet have a Record (resumable left-anti-join), and per page
call the LLM, persist Record + PersonMentions (+ deduped Place), computing blocking keys inline.

Cross-page entries: an act can start at the bottom of one sheet and continue at the top of the
next. Per-page extraction flags those halves (``incomplete`` / ``continues_from_previous``); a
second pass (``_stitch_spans``) re-extracts the boundary with the LLM and stores ONE consistent
record spanning both pages. A final pass (``_validate_sequence``) flags numbering gaps/duplicates.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..core import events
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.document import Document, Page
from ..models.job import Job
from ..models.mention import PersonMention
from ..models.place import Place
from ..models.record import Record
from ..models.transcription import Transcription
from ..modules.extraction.normalize import compute_keys, normalize_role, split_name
from ..modules.extraction.schemas import SYSTEM_PROMPT, ExtractedPage, ExtractedRecord
from ..modules.providers.service import ProviderService, extract_structured_with_usage
from ..modules.tree.mapping import normalize_place

CONFIDENCE_FLOOR = 0.45
_ACTIVE = ("extracted", "needs_review", "reviewed")  # non-superseded, non-rejected


def _parse_page(raw: dict) -> ExtractedPage:
    """Tolerant parse of a model's page output. LLMs (esp. richer ones like Gemini Pro) occasionally
    emit one malformed record/field; salvage the records that validate individually instead of
    dropping the whole page, so a single bad field doesn't lose a page full of acts."""
    try:
        return ExtractedPage.model_validate(raw)
    except Exception:
        recs: list[ExtractedRecord] = []
        for r in (raw.get("records") or []) if isinstance(raw, dict) else []:
            try:
                recs.append(ExtractedRecord.model_validate(r))
            except Exception:
                continue
        folio = raw.get("folio_label") if isinstance(raw, dict) else None
        return ExtractedPage(has_record=bool(recs),
                             folio_label=folio if isinstance(folio, str) else None, records=recs)


def _folio_parse(label: str | None) -> tuple[int, str] | None:
    """Parse a folio label into (number, recto/verso suffix): '45v' → (45, 'v'), '145' → (145, '')."""
    if not label:
        return None
    m = re.search(r"(\d+)\s*([rvRV])?", str(label))
    if not m:
        return None
    return int(m.group(1)), (m.group(2) or "").lower()


async def _normalize_folios(session, *, document_id) -> int:
    """Folio (sheet) numbers run sequentially, so fill pages where the LLM didn't read a folio by
    extrapolating from the detected anchors. Handles the two regular patterns — one folio per page,
    or recto/verso (two pages per leaf). Conservative: does nothing unless the detected folios imply
    a clear, consistent step (so it won't invent numbers on irregular books). Returns #pages filled."""
    pages = (await session.scalars(
        select(Page).where(Page.document_id == document_id).order_by(Page.page_no)
    )).all()
    parsed = [(p, _folio_parse(p.folio_label)) for p in pages]
    anchors = [(p.page_no, v[0], v[1]) for p, v in parsed if v]
    if len(anchors) < 2:
        return 0
    (p0, n0, s0), (p1, n1, _) = anchors[0], anchors[-1]
    if p1 == p0:
        return 0
    step = (n1 - n0) / (p1 - p0)
    rv = any(s for _, _, s in anchors)
    if abs(step - 1.0) < 0.15:      # one sequential number per page
        per = 1
    elif rv and abs(step - 0.5) < 0.1:  # recto/verso: number advances every two pages
        per = 2
    else:
        return 0  # irregular — leave the LLM values untouched
    base = 2 * n0 + (0 if s0 != "v" else 1)  # phase the r/v alternation from the first anchor
    filled = 0
    for p, v in parsed:
        if v:
            continue
        if per == 1:
            p.folio_label = str(n0 + (p.page_no - p0))[:32]
        else:
            idx = base + (p.page_no - p0)
            p.folio_label = f"{idx // 2}{'r' if idx % 2 == 0 else 'v'}"[:32]
        filled += 1
    await session.flush()
    return filled


def _parse_seq(record_no: str | None) -> int | None:
    """Numeric value of an entry number for ordering/validation ('45 bis' → 45)."""
    if not record_no:
        return None
    m = re.search(r"\d+", str(record_no))
    return int(m.group()) if m else None


async def _resolve_place(session, tenant_id: uuid.UUID, raw: str | None) -> uuid.UUID | None:
    if not raw or not raw.strip():
        return None
    key = normalize_place(raw)[:512]
    pid = await session.scalar(
        select(Place.id).where(Place.tenant_id == tenant_id, Place.normalized_key == key)
    )
    if pid:
        return pid
    place = Place(tenant_id=tenant_id, name=raw.strip()[:512], normalized_key=key)
    session.add(place)
    await session.flush()
    return place.id


async def _persist_record(
    session, *, tenant_id, document_id, job_id, rc, visibility, rec: ExtractedRecord,
    page_id, transcription_id, position: int,
    page_end_id=None, transcription_end_id=None, is_continued: bool = False,
) -> tuple[Record, bool]:
    """Insert one Record + its PersonMentions. Returns (record, low_confidence_flag). The within-page
    reading position is stashed in raw_json (``_pos``) so the stitch pass can find the first/last
    record on a page."""
    place_id = await _resolve_place(session, tenant_id, rec.place_raw or rec.parish_raw)
    low_conf = rec.confidence < CONFIDENCE_FLOOR or not rec.mentions
    raw = rec.model_dump()
    raw["_pos"] = position
    record = Record(
        tenant_id=tenant_id, document_id=document_id, page_id=page_id,
        transcription_id=transcription_id, page_end_id=page_end_id,
        transcription_end_id=transcription_end_id, is_continued=is_continued,
        record_no=rec.record_no, record_seq=_parse_seq(rec.record_no), visibility=visibility,
        record_type=rec.record_type, date_raw=rec.date_raw, date_year=rec.date_year,
        date_month=rec.date_month, date_day=rec.date_day, place_id=place_id,
        parish_raw=rec.parish_raw, address=rec.address,
        household_key=rec.household_key, attributes=rec.attributes or None, summary=rec.summary,
        raw_json=raw, extraction_engine=rc.engine, extraction_model=rc.model,
        confidence=rec.confidence, status="needs_review" if low_conf else "extracted", job_id=job_id,
    )
    session.add(record)
    await session.flush()
    for m in rec.mentions:
        given = m.given or split_name(m.name_raw)[0] or None
        surname = m.surname or split_name(m.name_raw)[1] or None
        session.add(PersonMention(
            tenant_id=tenant_id, record_id=record.id, visibility=visibility,
            role=normalize_role(m.role), given=given, surname=surname, name_raw=m.name_raw,
            sex=(m.sex or "U")[:1].upper(), stated_age=m.stated_age, stated_origin=m.stated_origin,
            stated_status=m.stated_status, occupation=m.occupation, address=m.address,
            raw_json=m.model_dump(), **compute_keys(given, surname),
        ))
    return record, low_conf


def _merge_raw(a: dict, b: dict) -> ExtractedRecord:
    """Deterministic fallback when the boundary re-extraction LLM call fails: union the two halves,
    preferring the start half's non-null fields, concatenating summary, merging attributes/mentions."""
    out = dict(a)
    for k, v in b.items():
        if k.startswith("_"):
            continue
        if out.get(k) in (None, "", [], {}) and v not in (None, "", [], {}):
            out[k] = v
    out["summary"] = " ".join(s for s in (a.get("summary"), b.get("summary")) if s) or None
    out["attributes"] = {**(b.get("attributes") or {}), **(a.get("attributes") or {})}
    out["mentions"] = (a.get("mentions") or []) + (b.get("mentions") or [])
    out["incomplete"] = False
    out["continues_from_previous"] = False
    out.pop("_pos", None)
    return ExtractedRecord.model_validate(out)


_STITCH_SYSTEM = (
    "Estas son dos mitades de UNA MISMA entrada de un registro histórico, partida entre el final "
    "de una hoja y el principio de la siguiente. Reconstruye y extrae ESE ÚNICO acto completo "
    "combinando ambas mitades; devuelve exactamente UN registro en 'records'.\n" + SYSTEM_PROMPT
)


async def merge_boundary_records(
    session, *, tenant_id, document_id, rc, schema, start_rec: Record, end_rec: Record,
    start_text: str, end_text: str, job_id=None,
) -> Record:
    """Re-extract the boundary (tail of start page + head of next page) into ONE complete record that
    spans both pages, then delete the two half-records (mentions cascade). Deterministic field-union
    fallback if the LLM call fails. Shared by the auto-stitch pass and the manual merge endpoint."""
    tail, head = (start_text or "")[-2000:], (end_text or "")[:2000]
    merged: ExtractedRecord | None = None
    try:
        raw, _ = await asyncio.to_thread(
            extract_structured_with_usage, rc,
            f"FINAL DE LA HOJA:\n{tail}\n\nPRINCIPIO DE LA SIGUIENTE:\n{head}",
            schema=schema, system=_STITCH_SYSTEM, schema_name="ExtractedPage",
        )
        pg = _parse_page(raw)
        if pg.records:
            merged = pg.records[0]
    except Exception:
        merged = None
    if merged is None:
        merged = _merge_raw(start_rec.raw_json or {}, end_rec.raw_json or {})
    merged.incomplete = False
    merged.continues_from_previous = False
    if not merged.record_no:
        merged.record_no = start_rec.record_no or end_rec.record_no

    new_rec, _ = await _persist_record(
        session, tenant_id=tenant_id, document_id=document_id, job_id=job_id, rc=rc,
        visibility=start_rec.visibility, rec=merged,
        page_id=start_rec.page_id, transcription_id=start_rec.transcription_id,
        page_end_id=end_rec.page_id, transcription_end_id=end_rec.transcription_id,
        is_continued=True, position=(start_rec.raw_json or {}).get("_pos", 0),
    )
    await session.delete(start_rec)
    await session.delete(end_rec)
    await session.flush()
    return new_rec


async def _stitch_spans(session, *, tenant_id, document_id, job_id, rc, schema, pub) -> int:
    """Join entries split across a page break into a single consistent record. Returns #merges."""
    pages = (await session.scalars(
        select(Page).where(Page.document_id == document_id)
    )).all()
    txs = (await session.scalars(
        select(Transcription).where(
            Transcription.document_id == document_id, Transcription.is_active.is_(True)
        )
    )).all()
    no_to_text = {t.page_no: (t.text or "") for t in txs}

    recs = (await session.scalars(
        select(Record).where(Record.document_id == document_id, Record.status.in_(_ACTIVE))
    )).all()
    pid_to_no = {p.id: p.page_no for p in pages}
    by_page: dict[int, list[Record]] = {}
    for r in recs:
        pno = pid_to_no.get(r.page_id)
        if pno is not None:
            by_page.setdefault(pno, []).append(r)
    for lst in by_page.values():
        lst.sort(key=lambda r: (r.raw_json or {}).get("_pos", 0))

    merges = 0
    for pno in sorted(by_page):
        nxt = pno + 1
        if nxt not in by_page:
            continue
        last = by_page[pno][-1] if by_page[pno] else None
        first = by_page[nxt][0] if by_page[nxt] else None
        if not last or not first or last.is_continued:
            continue
        if (last.attributes or {}).get("manual_split") or (first.attributes or {}).get("manual_split"):
            continue  # user has unlinked this boundary by hand
        if not (last.raw_json or {}).get("incomplete") or not (first.raw_json or {}).get("continues_from_previous"):
            continue
        await merge_boundary_records(
            session, tenant_id=tenant_id, document_id=document_id, rc=rc, schema=schema,
            start_rec=last, end_rec=first,
            start_text=no_to_text.get(pno, ""), end_text=no_to_text.get(nxt, ""), job_id=job_id,
        )
        merges += 1
        await pub({"kind": "stitch", "page": pno})
    return merges


async def _validate_sequence(session, *, document_id) -> int:
    """Flag duplicate or missing entry numbers as needs_review. Registers RENUMBER each year, so we
    group by date_year before comparing — otherwise 'acta 49 de 1924' and 'acta 49 de 1927' would look
    like a false duplicate. Returns #records flagged."""
    from collections import defaultdict

    recs = (await session.scalars(
        select(Record).where(
            Record.document_id == document_id, Record.status.in_(_ACTIVE),
            Record.record_seq.is_not(None),
        )
    )).all()
    groups: dict[object, list[Record]] = defaultdict(list)
    for r in recs:
        groups[r.date_year].append(r)  # date_year=None is its own bucket (unknown year)

    flagged = 0
    for grp in groups.values():
        grp.sort(key=lambda r: r.record_seq)
        prev: Record | None = None
        for r in grp:
            warn: str | None = None
            if prev is not None and prev is not r:
                d = r.record_seq - prev.record_seq
                if d == 0:
                    warn = f"acta {r.record_no} duplicada"
                elif 1 < d <= 3:  # small skip within the same year = likely missing entries
                    missing = ", ".join(str(n) for n in range(prev.record_seq + 1, r.record_seq))
                    warn = f"falta(n) el/los acta(s) {missing}"
            if warn:
                attrs = dict(r.attributes or {})
                attrs["sequence_warning"] = warn
                r.attributes = attrs
                if r.status == "extracted":
                    r.status = "needs_review"
                flagged += 1
            prev = r
    await session.flush()
    return flagged


async def _run_batch_extraction(session, *, job_id, tenant_id, document_id, rc, targets, doc_context, schema, pub):
    """Batch API path: submit one request per page, poll until done, then persist + run phases B/C.
    Resumable — the batch id is stored on the job, so a worker restart resumes polling instead of
    resubmitting. (Validated structurally; needs a billed OpenAI/Google key to exercise end-to-end.)"""
    from ..modules.providers.service import fetch_batch_results, poll_batch, submit_chat_batch

    by_tid = {str(t[0]): t for t in targets}
    job = await session.get(Job, job_id)
    batch_id = (job.params or {}).get("batch_id") if job else None
    if not batch_id:
        items = [(str(tid), doc_context + text) for (tid, _pid, _pno, text, _vis) in targets]
        batch_id = await asyncio.to_thread(submit_chat_batch, rc, items, system=SYSTEM_PROMPT)
        params = dict(job.params or {}); params["batch_id"] = batch_id
        await session.execute(update(Job).where(Job.id == job_id).values(
            params=params, progress={"phase": "batch_submitted", "total": len(targets)}))
        await session.commit()
        await pub({"kind": "page_ok", "done": 0, "total": len(targets), "phase": "batch enviado"})

    status, out_file = "validating", None
    for _ in range(240):  # poll ~4h max (under job_timeout); batch id persists for a later resume
        await set_rls_context(session, tenant_id=tenant_id)
        if await session.scalar(select(Job.status).where(Job.id == job_id)) == "cancelled":
            await pub({"kind": "cancelled"}); return
        status, out_file = await asyncio.to_thread(poll_batch, rc, batch_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            progress={"phase": status, "total": len(targets)}))
        await session.commit()
        if status in ("completed", "failed", "expired", "cancelled"):
            break
        await asyncio.sleep(60)
    if status != "completed" or not out_file:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(update(Job).where(Job.id == job_id).values(
            status="error", error=f"batch {status}", finished_at=datetime.now(timezone.utc)))
        await session.commit()
        await pub({"kind": "book_fail", "error": f"batch {status}"}); return

    results = await asyncio.to_thread(fetch_batch_results, rc, out_file)
    await set_rls_context(session, tenant_id=tenant_id)
    records_made = failed = flagged = 0
    for tid, raw in results.items():
        tgt = by_tid.get(tid)
        if tgt is None:
            continue
        _, page_id, _pno, _text, visibility = tgt
        if raw is None:
            failed += 1; continue
        try:
            page = _parse_page(raw)
        except Exception:
            failed += 1; continue
        if page.folio_label and page_id:
            await session.execute(update(Page).where(Page.id == page_id).values(folio_label=page.folio_label[:32]))
        if page.records:  # gate on records present (robust to models omitting has_record)
            for i, rec in enumerate(page.records):
                _, low = await _persist_record(
                    session, tenant_id=tenant_id, document_id=document_id, job_id=job_id, rc=rc,
                    visibility=visibility, rec=rec, page_id=page_id,
                    transcription_id=uuid.UUID(tid), position=i)
                records_made += 1
                flagged += 1 if low else 0
    await session.commit()

    await set_rls_context(session, tenant_id=tenant_id)
    folios = await _normalize_folios(session, document_id=document_id)
    merges = await _stitch_spans(session, tenant_id=tenant_id, document_id=document_id, job_id=job_id,
                                 rc=rc, schema=schema, pub=pub)
    seq = await _validate_sequence(session, document_id=document_id)
    await session.commit()

    final_status = "error" if failed and failed > len(results) * 0.3 else "completed"
    await set_rls_context(session, tenant_id=tenant_id)
    await session.execute(update(Job).where(Job.id == job_id).values(
        status=final_status, finished_at=datetime.now(timezone.utc),
        error=(f"{failed}/{len(results)} páginas fallaron" if final_status == "error" else None),
        result={"pages": len(results), "records": records_made, "failed": failed,
                "needs_review": flagged + seq, "stitched": merges, "sequence_flags": seq,
                "folios_filled": folios, "modality": "batch", "model": rc.model}))
    await session.commit()
    if records_made:
        try:
            from ..core.queue import get_queue
            ej = Job(tenant_id=tenant_id, type="embed_mentions", status="queued",
                     params={"document_id": str(document_id)})
            session.add(ej); await session.commit()
            q = await get_queue()
            await q.enqueue_job("embed_mentions", job_id=str(ej.id), tenant_id=str(tenant_id),
                                document_id=str(document_id))
        except Exception:
            pass
    await pub({"kind": "book_fail" if final_status == "error" else "all_done",
               "pages": len(results), "records": records_made, "failed": failed, "stitched": merges})


async def extract_records(ctx, *, job_id, tenant_id, document_id, override=None, options=None):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    document_id = uuid.UUID(str(document_id))
    override = override or {}

    async def pub(event: dict) -> None:
        await events.publish(tenant_id, job_id, event)

    schema = ExtractedPage.model_json_schema()

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
            rc = await ProviderService(session).resolve(
                tenant_id=tenant_id, task_type="inference", override=override
            )
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

        # resumable: skip transcriptions that already produced a (non-superseded) record — either as
        # the start (transcription_id) OR the end of a span (transcription_end_id), so the 2nd sheet
        # of a stitched entry isn't re-extracted as if empty. A corrected page (records superseded)
        # falls back in.
        done_start = select(Record.transcription_id).where(
            Record.document_id == document_id, Record.status != "superseded"
        )
        done_end = select(Record.transcription_end_id).where(
            Record.document_id == document_id, Record.status != "superseded",
            Record.transcription_end_id.is_not(None),
        )
        # Skip index/cover/blank pages — they aren't acts. Index pages are parsed separately.
        nonrecord_pages = select(Page.id).where(
            Page.document_id == document_id, Page.kind != "record"
        )
        rows = (
            await session.scalars(
                select(Transcription).where(
                    Transcription.document_id == document_id,
                    Transcription.text.is_not(None),
                    Transcription.is_active.is_(True),
                    Transcription.id.not_in(done_start),
                    Transcription.id.not_in(done_end),
                    Transcription.page_id.not_in(nonrecord_pages),
                )
            )
        ).all()
        targets = [(t.id, t.page_id, t.page_no, t.text, t.visibility) for t in rows]

        # ground the LLM in the book's declared type/municipality/years → constrains inference.
        from ..modules.extraction.record_types import type_hint
        doc = await session.get(Document, document_id)
        ctx_bits: list[str] = []
        if doc and doc.default_record_type:
            ctx_bits.append(type_hint(doc.default_record_type))
        if doc and doc.place_id:
            pl = await session.scalar(select(Place.name).where(Place.id == doc.place_id))
            if pl:
                ctx_bits.append(f"Municipio del libro: {pl}.")
        if doc and (doc.year_from or doc.year_to):
            ctx_bits.append(f"Años que abarca el libro: {doc.year_from or '?'}–{doc.year_to or '?'}.")
        doc_context = ("CONTEXTO DEL LIBRO (úsalo para acotar y no inventar) — " + " ".join(ctx_bits) + "\n\n") if ctx_bits else ""
        await session.commit()

        # Modality: Batch API (async, ~50% cheaper) when chosen and the provider supports it.
        from ..modules.providers.catalog import PROVIDER_CATALOG
        modality = (options or {}).get("modality", "sync")
        if modality == "batch" and PROVIDER_CATALOG.get(rc.engine, {}).get("batch"):
            await _run_batch_extraction(
                session, job_id=job_id, tenant_id=tenant_id, document_id=document_id,
                rc=rc, targets=targets, doc_context=doc_context, schema=schema, pub=pub)
            return
        elif modality == "batch":
            await pub({"kind": "log", "message": f"{rc.engine} no soporta Batch; usando modo síncrono"})

        total = len(targets)
        done = 0
        records_made = 0
        flagged = 0
        failed = 0
        last_error: str | None = None
        tokens = {"prompt": 0, "completion": 0, "total": 0}  # M3 cost probe
        await pub({"kind": "book_start", "total": total, "engine": rc.engine})

        CONCURRENCY = 6  # parallel page extractions per chunk (bounded for provider rate limits)
        for start in range(0, total, CONCURRENCY):
            chunk = targets[start : start + CONCURRENCY]
            await set_rls_context(session, tenant_id=tenant_id)
            if await session.scalar(select(Job.status).where(Job.id == job_id)) == "cancelled":
                await pub({"kind": "cancelled", "done": done, "total": total})
                return

            # run the page extractions in parallel (pure LLM calls, no DB)
            results = await asyncio.gather(*[
                asyncio.to_thread(
                    extract_structured_with_usage, rc, doc_context + t,
                    schema=schema, system=SYSTEM_PROMPT, schema_name="ExtractedPage",
                )
                for (_tid, _pid, _pno, t, _vis) in chunk
            ], return_exceptions=True)

            # persist results serially through the one session
            await set_rls_context(session, tenant_id=tenant_id)
            for (tid, page_id, _page_no, _text, visibility), res in zip(chunk, results):
                done += 1
                if isinstance(res, Exception):
                    failed += 1
                    last_error = str(res)[:200]
                    await pub({"kind": "page_fail", "transcription_id": str(tid), "error": last_error})
                    continue
                raw, usage = res
                for k in tokens:
                    tokens[k] += usage.get(k, 0)
                try:
                    page = _parse_page(raw)
                except Exception as exc:
                    failed += 1
                    last_error = str(exc)[:200]
                    await pub({"kind": "page_fail", "transcription_id": str(tid), "error": last_error})
                    continue
                if page.folio_label and page_id:
                    await session.execute(
                        update(Page).where(Page.id == page_id).values(folio_label=page.folio_label[:32])
                    )
                if page.records:  # gate on records present (robust to models omitting has_record)
                    for i, rec in enumerate(page.records):
                        _, low = await _persist_record(
                            session, tenant_id=tenant_id, document_id=document_id, job_id=job_id,
                            rc=rc, visibility=visibility, rec=rec,
                            page_id=page_id, transcription_id=tid, position=i,
                        )
                        records_made += 1
                        if low:
                            flagged += 1
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    progress={"done": done, "total": total, "records": records_made, "failed": failed}
                )
            )
            await session.commit()
            await pub({"kind": "page_ok", "done": done, "total": total, "records": records_made, "failed": failed})

        # Phase B/C: fill sequential folios, stitch cross-page entries, validate entry numbering.
        await set_rls_context(session, tenant_id=tenant_id)
        folios_filled = await _normalize_folios(session, document_id=document_id)
        merges = await _stitch_spans(
            session, tenant_id=tenant_id, document_id=document_id, job_id=job_id,
            rc=rc, schema=schema, pub=pub,
        )
        seq_flagged = await _validate_sequence(session, document_id=document_id)
        await session.commit()

        per_page = round(tokens["total"] / (done - failed), 1) if (done - failed) else 0
        # If most pages failed (e.g. provider out of credit / rate-limited), the run is NOT a clean
        # success — mark it 'error' so the UI flags it and the user can switch provider and re-run
        # (the anti-join reprocesses the failed pages). A few failures stay 'completed'.
        final_status = "error" if failed and failed > done * 0.3 else "completed"
        err_msg = (f"{failed}/{done} páginas fallaron en la extracción · ej.: {last_error}"
                   if final_status == "error" else None)
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status=final_status, error=err_msg, finished_at=datetime.now(timezone.utc),
                result={
                    "pages": done, "records": records_made, "failed": failed,
                    "needs_review": flagged + seq_flagged,
                    "stitched": merges, "sequence_flags": seq_flagged, "folios_filled": folios_filled,
                    "tokens": tokens, "tokens_per_page": per_page, "model": rc.model,
                    "last_error": last_error,
                },
            )
        )
        await session.commit()

        # Log AI spend for the spending control (best-effort).
        from ..modules.providers.service import record_usage
        await set_rls_context(session, tenant_id=tenant_id)
        await record_usage(session, tenant_id=tenant_id, job_id=job_id, task_type="extraction",
                           model=rc.model, prompt_tokens=tokens["prompt"], completion_tokens=tokens["completion"])
        await session.commit()

        # Auto-embed the new mentions so semantic search + vector linkage work without a manual step.
        if records_made:
            try:
                from ..core.queue import get_queue
                await set_rls_context(session, tenant_id=tenant_id)
                embed_job = Job(
                    tenant_id=tenant_id, type="embed_mentions", status="queued",
                    params={"document_id": str(document_id)},
                )
                session.add(embed_job)
                await session.commit()  # durable before enqueue
                eid = embed_job.id
                queue = await get_queue()
                await queue.enqueue_job(
                    "embed_mentions", job_id=str(eid), tenant_id=str(tenant_id),
                    document_id=str(document_id),
                )
            except Exception:  # embedding is best-effort; never fail the extraction over it
                pass

        await pub({
            "kind": "book_fail" if final_status == "error" else "all_done",
            "pages": done, "records": records_made, "failed": failed,
            "needs_review": flagged + seq_flagged, "stitched": merges,
            "tokens_per_page": per_page, "error": err_msg,
        })
