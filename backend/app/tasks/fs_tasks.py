"""ARQ job: download a FamilySearch catalog/film/ARK into a tenant-private document.

Reuses fs_core verbatim for the FamilySearch protocol (auth, URL parsing, image resolution,
metadata, link extraction); only the storage sink is adapted from disk to MinIO. Gated by the
FamilySearch connector, which is only mounted when FS_CONNECTOR_ENABLED is set.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..core import events, storage
from ..core.crypto import get_secret_box
from ..db.rls import set_rls_context
from ..db.session import SessionLocal
from ..models.connector import ConnectorCredential
from ..models.document import Document, Page
from ..models.job import Job

MAX_IMAGES = 2000  # safety cap against runaway loops
MAX_CONSEC_ERRORS = 10


def _load_fs_session(cookies_json: str):
    from fs_core import authenticate_from_cookies_file, build_session

    session = build_session()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        tf.write(cookies_json)
        path = tf.name
    try:
        if not authenticate_from_cookies_file(session, path):
            raise RuntimeError("FamilySearch auth failed (invalid cookies / missing fssessionid)")
    finally:
        os.unlink(path)
    return session


def _fetch_image(session, iid: str) -> dict:
    from fs_core import (
        extract_ark,
        extract_download_url,
        extract_next_iid,
        get_image_metadata,
    )

    meta = get_image_metadata(session, iid)
    if not meta:
        return {"status": "error", "error": "no metadata"}
    if meta.get("_end"):
        return {"status": "end"}
    if meta.get("_unauth"):
        return {"status": "unauth"}
    nxt, ark = extract_next_iid(meta), extract_ark(meta)
    if meta.get("_restricted"):
        return {"status": "restricted", "next": nxt}
    url = extract_download_url(meta)
    if not url:
        return {"status": "skip", "next": nxt}
    r = session.get(url, stream=True, timeout=45, allow_redirects=True)
    if r.status_code != 200:
        return {"status": "error", "error": f"HTTP {r.status_code}", "next": nxt}
    return {"status": "ok", "data": r.content, "next": nxt, "ark": ark}


async def _fail(session, tenant_id, job_id, msg: str) -> None:
    await set_rls_context(session, tenant_id=tenant_id)
    await session.execute(
        update(Job).where(Job.id == job_id).values(
            status="error", error=msg[:1000], finished_at=datetime.now(timezone.utc)
        )
    )
    await session.commit()


async def fs_download(ctx, *, job_id, tenant_id, url, settings=None):
    job_id = uuid.UUID(str(job_id))
    tenant_id = uuid.UUID(str(tenant_id))
    settings = settings or {}
    max_images = int(settings.get("max_images") or MAX_IMAGES) or MAX_IMAGES
    delay = float(settings.get("delay") or 1.0)

    async def pub(event: dict) -> None:
        try:
            await events.publish(tenant_id, job_id, event)
        except Exception:
            pass  # progress is best-effort; a Redis blip must not fail the download

    # Defence-in-depth SSRF guard (the API also validates): only https familysearch.org.
    from urllib.parse import urlparse

    _host = (urlparse(str(url)).hostname or "").lower()
    if urlparse(str(url)).scheme != "https" or not (
        _host == "familysearch.org" or _host.endswith(".familysearch.org")
    ):
        async with SessionLocal() as session:
            await _fail(session, tenant_id, job_id, "URL no permitida (debe ser https familysearch.org)")
        await pub({"kind": "book_fail", "error": "url not allowed"})
        return

    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="running", started_at=datetime.now(timezone.utc)
            )
        )
        await session.commit()

        # connector_credentials are operator/server-scoped (FS cookies belong to the operator). The
        # RLS read policy is server-admin-only, so read them with the server-admin GUC set — this is
        # trusted server-side code acting on the operator's behalf, not a tenant user.
        await set_rls_context(session, tenant_id=tenant_id, is_server_admin=True)
        cred = await session.scalar(
            select(ConnectorCredential).where(
                ConnectorCredential.connector == "familysearch",
                ConnectorCredential.is_active.is_(True),
            )
        )
        await session.commit()
        if not cred:
            await _fail(session, tenant_id, job_id, "no active FamilySearch credential (server-admin must add cookies)")
            await pub({"kind": "book_fail", "error": "no credential"})
            return
        cookies_json = get_secret_box().decrypt(cred.secret_ciphertext, cred.secret_nonce)

        doc_id = uuid.uuid4()
        bucket = storage.bucket_for("private")
        prefix = f"{tenant_id}/{doc_id}/"
        await set_rls_context(session, tenant_id=tenant_id)
        session.add(
            Document(
                id=doc_id, tenant_id=tenant_id, title=f"FamilySearch: {url[:100]}",
                doc_type="image_set", visibility="private", source_kind="familysearch",
                storage_bucket=bucket, storage_prefix=prefix, page_count=0,
                source_ref=url,  # full origin URL for end-to-end traceability
                created_by=cred.created_by,
            )
        )
        await session.commit()

        try:
            from fs_core import parse_fs_url, resolve_first_image_ark

            fs = await asyncio.to_thread(_load_fs_session, cookies_json)
            url_info = parse_fs_url(url)
            iid = await asyncio.to_thread(resolve_first_image_ark, fs, url_info)
        except Exception as exc:
            await _fail(session, tenant_id, job_id, str(exc))
            await pub({"kind": "book_fail", "error": str(exc)[:300]})
            return
        if not iid:
            await _fail(session, tenant_id, job_id, "could not resolve first image (check URL/session)")
            await pub({"kind": "book_fail", "error": "no first image"})
            return

        await pub({"kind": "book_start", "total": 0})
        index = errors = consec = 0
        while iid and index < max_images:
            await set_rls_context(session, tenant_id=tenant_id)
            st = await session.scalar(select(Job.status).where(Job.id == job_id))
            await session.commit()
            if st == "cancelled":
                await pub({"kind": "log", "message": "cancelled"})
                break

            res = await asyncio.to_thread(_fetch_image, fs, iid)
            status = res["status"]
            if status == "end":
                break
            if status == "unauth":
                await _fail(session, tenant_id, job_id, "FamilySearch session expired")
                await pub({"kind": "book_fail", "error": "session expired"})
                return
            if status == "ok":
                index += 1
                consec = 0
                key = f"{prefix}pages/{index}.jpg"
                await storage.put_object(bucket, key, res["data"], "image/jpeg")
                await set_rls_context(session, tenant_id=tenant_id)
                session.add(
                    Page(
                        tenant_id=tenant_id, document_id=doc_id, visibility="private",
                        page_no=index, storage_key=key, content_type="image/jpeg",
                        byte_size=len(res["data"]), source_ref=str(iid),  # exact image ARK
                    )
                )
                await session.execute(
                    update(Job).where(Job.id == job_id).values(progress={"downloaded": index, "errors": errors})
                )
                await session.commit()
                await pub({"kind": "page_ok", "page_no": index})
            elif status in ("restricted", "skip"):
                await pub({"kind": "page_fail", "error": status})
            else:
                errors += 1
                consec += 1
                await pub({"kind": "page_fail", "error": res.get("error", "error")})
                if consec >= MAX_CONSEC_ERRORS:
                    break

            iid = res.get("next")
            if delay:
                await asyncio.sleep(delay)

        await set_rls_context(session, tenant_id=tenant_id)
        if index == 0:
            # Nothing came down (bad URL, expired cookies, restricted film…). A green "completado"
            # with an empty book would be a lie — fail the job and remove the empty shell document.
            from sqlalchemy import delete as sa_delete
            await session.execute(sa_delete(Document).where(Document.id == doc_id))
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    status="error", finished_at=datetime.now(timezone.utc),
                    error="No se descargó ninguna imagen — revisa la URL y que la sesión de FamilySearch (cookies) siga siendo válida.",
                    result={"downloaded": 0, "errors": errors},
                )
            )
            await session.commit()
            await pub({"kind": "book_fail", "error": "no se descargó ninguna imagen"})
            return
        await session.execute(update(Document).where(Document.id == doc_id).values(page_count=index))
        await session.execute(
            update(Job).where(Job.id == job_id).values(
                status="completed", finished_at=datetime.now(timezone.utc),
                result={"document_id": str(doc_id), "downloaded": index, "errors": errors},
            )
        )
        await session.commit()
        await pub({"kind": "all_done", "downloaded": index, "errors": errors, "document_id": str(doc_id)})
