"""S3-compatible object storage (MinIO now, any S3 later).

boto3 is synchronous; we wrap calls in ``asyncio.to_thread`` so they don't block the event
loop. Object content is streamed back through the API (auth + RLS enforced) rather than via
presigned URLs, so MinIO stays internal and private documents stay access-controlled.

Key layout:  {tenant_id}/{document_id}/original.pdf
             {tenant_id}/{document_id}/pages/{page_no}{ext}
"""
from __future__ import annotations

import asyncio

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from ..settings import settings

_client = None
_backup_client = None


def _s3():
    global _client
    if _client is None:
        scheme = "https" if settings.minio_secure else "http"
        cfg: dict = {"signature_version": "s3v4", "retries": {"total_max_attempts": 1, "mode": "standard"}}
        if settings.minio_addressing_style:  # "path" for MinIO/Backblaze; "" lets boto3 decide
            cfg["s3"] = {"addressing_style": settings.minio_addressing_style}
        _client = boto3.client(
            "s3",
            endpoint_url=f"{scheme}://{settings.minio_endpoint}",
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            config=Config(**cfg),
            region_name=settings.minio_region,
        )
    return _client


def backup_s3():
    """Client for the isolated, off-site backup store."""
    global _backup_client
    if _backup_client is None:
        scheme = "https" if settings.backup_s3_secure else "http"
        cfg: dict = {"signature_version": "s3v4"}
        if settings.backup_s3_addressing_style:
            cfg["s3"] = {"addressing_style": settings.backup_s3_addressing_style}
        _backup_client = boto3.client(
            "s3", endpoint_url=f"{scheme}://{settings.backup_s3_endpoint}",
            aws_access_key_id=settings.backup_s3_access_key,
            aws_secret_access_key=settings.backup_s3_secret_key,
            config=Config(**cfg), region_name=settings.backup_s3_region,
        )
    return _backup_client


def bucket_for(visibility: str) -> str:
    return settings.minio_bucket_public if visibility == "public" else settings.minio_bucket_private


async def ensure_buckets() -> None:
    def _ensure() -> None:
        s3 = _s3()
        # AWS S3 requires a LocationConstraint to create a bucket outside us-east-1; MinIO ignores it.
        mk: dict = {}
        if settings.minio_region and settings.minio_region != "us-east-1":
            mk["CreateBucketConfiguration"] = {"LocationConstraint": settings.minio_region}
        for bucket in (settings.minio_bucket_private, settings.minio_bucket_public):
            try:
                s3.head_bucket(Bucket=bucket)
            except ClientError:
                # external S3 with restricted creds may forbid create — pre-create the buckets there
                try:
                    s3.create_bucket(Bucket=bucket, **mk)
                except ClientError:
                    pass

    await asyncio.to_thread(_ensure)


async def check_ready() -> None:
    """Verify both application buckets before declaring this process ready."""
    def _check() -> None:
        s3 = _s3()
        for bucket in (settings.minio_bucket_private, settings.minio_bucket_public):
            s3.head_bucket(Bucket=bucket)
    await asyncio.to_thread(_check)


async def put_object(bucket: str, key: str, data: bytes, content_type: str, *, write_id=None) -> None:
    from .storage_transfers import put
    await put(_s3(), bucket, key, data, content_type, write_id=write_id)


async def upload_file(bucket: str, key: str, path: str, content_type: str) -> None:
    from .storage_transfers import upload
    await upload(_s3(), bucket, key, path, content_type)


async def download_file(bucket: str, key: str, path: str) -> None:
    await asyncio.to_thread(_s3().download_file, bucket, key, path)


async def open_object(bucket: str, key: str, *, byte_range: str | None = None):
    options = {"Range": byte_range} if byte_range else {}
    return await asyncio.to_thread(_s3().get_object, Bucket=bucket, Key=key, **options)


async def get_object(bucket: str, key: str) -> tuple[bytes, str]:
    def _get() -> tuple[bytes, str]:
        obj = _s3().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")

    return await asyncio.to_thread(_get)


async def copy_object(src_bucket: str, dst_bucket: str, key: str) -> None:
    from .storage_transfers import copy
    await copy(_s3(), src_bucket, dst_bucket, key)


async def move_prefix(src_bucket: str, dst_bucket: str, prefix: str) -> None:
    """Copy every object under ``prefix`` from src to dst, then delete from src.

    Used when (un)publishing a document moves it between the private and public buckets.
    """

    async for key in _prefix_keys(src_bucket, prefix):
        await copy_object(src_bucket, dst_bucket, key)
        await delete_object(src_bucket, key)


async def copy_prefix(src_bucket: str, dst_bucket: str, prefix: str) -> None:
    """Copy without deleting the source, so a failed DB transaction cannot lose originals."""
    async for key in _prefix_keys(src_bucket, prefix):
        await copy_object(src_bucket, dst_bucket, key)


async def delete_object(bucket: str, key: str, *, version_id=None) -> None:
    """Delete one exact object (use ``delete_prefix`` to remove a whole subtree)."""
    from .storage_transfers import delete
    await delete(_s3(), bucket, key, version_id=version_id)


async def delete_prefix(bucket: str, prefix: str) -> None:
    async for key in _prefix_keys(bucket, prefix):
        await delete_object(bucket, key)


async def _prefix_keys(bucket, prefix):
    from ..modules.storage_audit import inventory
    from ..modules.storage_audit.quota import scope
    scope(bucket, prefix)
    cursor = None
    previous_key = None
    while True:
        page = await asyncio.to_thread(_s3().list_objects_v2, Bucket=bucket, Prefix=prefix, MaxKeys=100,
            **({"ContinuationToken": cursor} if cursor else {}))
        if page.get("Name") != bucket or page.get("Prefix") != prefix or type(page.get("IsTruncated")) is not bool:
            raise ValueError("Listado de objetos incompleto")
        objects = page.get("Contents", [])
        if not isinstance(objects, list) or len(objects) > 100:
            raise ValueError("El listado supera el límite de página")
        keys = []
        for obj in objects:
            key = obj.get("Key") if isinstance(obj, dict) else None
            if not isinstance(key, str) or not key.startswith(prefix): raise ValueError("Objeto fuera del prefijo solicitado")
            scope(bucket, key)
            # General-purpose S3 buckets are ordered. Fail closed on overlapping
            # pages/cycles without keeping the complete key set in memory.
            if previous_key is not None and key <= previous_key:
                raise ValueError("El listado no avanza en orden de claves")
            previous_key = key
            keys.append(key)
        for key in keys:
            yield key
        if not page["IsTruncated"]: return
        following = page.get("NextContinuationToken")
        inventory.opaque(following, 2048)
        if not keys or following == cursor: raise ValueError("El listado no avanza")
        cursor = following
