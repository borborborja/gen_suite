"""Person photos: upload (resized) to the private MinIO bucket, list, stream, edit, delete.

Blobs are written before the DB row (a failed insert just orphans a blob, reclaimable later) and
streamed back through the API so they stay access-controlled — same approach as document pages.
"""
from __future__ import annotations

import io
import uuid

from fastapi import HTTPException, status
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import storage
from ...models.media import Media
from ...models.person import Person

MAX_DIM = 1600  # longest side; portraits don't need more


def _resize(data: bytes) -> tuple[bytes, str]:
    """Decode, EXIF-orient, downscale to MAX_DIM, re-encode as JPEG."""
    img = Image.open(io.BytesIO(data))
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGB")
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


async def upload_media(session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID,
                       data: bytes, content_type: str | None, caption: str | None) -> Media:
    person = await session.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
    if not (content_type or "").startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only image uploads are allowed")
    try:
        body, ctype = _resize(data)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "could not read image")
    key = f"{tenant_id}/person_media/{person_id}/{uuid.uuid4().hex}.jpg"
    await storage.put_object(storage.bucket_for("private"), key, body, ctype)
    # first photo for the person becomes the primary (avatar)
    has_any = await session.scalar(
        select(Media.id).where(Media.person_id == person_id).limit(1)
    )
    m = Media(tenant_id=tenant_id, person_id=person_id, storage_key=key, content_type=ctype,
              caption=(caption or None), is_primary=not has_any)
    session.add(m)
    await session.flush()
    return m


async def list_media(session: AsyncSession, person_id: uuid.UUID) -> list[Media]:
    return list((await session.scalars(
        select(Media).where(Media.person_id == person_id)
        .order_by(Media.is_primary.desc(), Media.created_at)
    )).all())


async def get_media_blob(session: AsyncSession, media_id: uuid.UUID) -> tuple[bytes, str]:
    m = await session.get(Media, media_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    return await storage.get_object(storage.bucket_for("private"), m.storage_key)


async def update_media(session: AsyncSession, media_id: uuid.UUID, *, caption=None,
                       make_primary: bool = False) -> Media:
    m = await session.get(Media, media_id)
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    if caption is not None:
        m.caption = caption or None
    if make_primary:
        await session.execute(
            update(Media).where(Media.person_id == m.person_id).values(is_primary=False)
        )
        m.is_primary = True
    await session.flush()
    return m


async def delete_media(session: AsyncSession, media_id: uuid.UUID) -> None:
    m = await session.get(Media, media_id)
    if not m:
        return
    was_primary, person_id, key = m.is_primary, m.person_id, m.storage_key
    await session.delete(m)
    await session.flush()
    try:
        await storage.delete_object(storage.bucket_for("private"), key)
    except Exception:
        pass  # orphan blob is harmless; sweeper reclaims it
    if was_primary:  # promote another photo to primary so the avatar survives
        nxt = await session.scalar(
            select(Media).where(Media.person_id == person_id).order_by(Media.created_at).limit(1)
        )
        if nxt:
            nxt.is_primary = True
            await session.flush()
