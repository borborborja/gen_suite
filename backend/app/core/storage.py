"""S3-compatible object storage (MinIO now, any S3 later).

boto3 is synchronous; we wrap calls in ``asyncio.to_thread`` so they don't block the event
loop. Object content is streamed back through the API (auth + RLS enforced) rather than via
presigned URLs, so MinIO stays internal and private documents stay access-controlled.

Key layout:  {tenant_id}/{document_id}/original/{filename}
             {tenant_id}/{document_id}/pages/{page_no}{ext}
"""
from __future__ import annotations

import asyncio

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from ..settings import settings

_client = None


def _s3():
    global _client
    if _client is None:
        scheme = "https" if settings.minio_secure else "http"
        cfg: dict = {"signature_version": "s3v4"}
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


async def put_object(bucket: str, key: str, data: bytes, content_type: str) -> None:
    await asyncio.to_thread(
        lambda: _s3().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    )


async def get_object(bucket: str, key: str) -> tuple[bytes, str]:
    def _get() -> tuple[bytes, str]:
        obj = _s3().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")

    return await asyncio.to_thread(_get)


async def copy_object(src_bucket: str, dst_bucket: str, key: str) -> None:
    await asyncio.to_thread(
        lambda: _s3().copy_object(
            Bucket=dst_bucket, Key=key, CopySource={"Bucket": src_bucket, "Key": key}
        )
    )


async def move_prefix(src_bucket: str, dst_bucket: str, prefix: str) -> None:
    """Copy every object under ``prefix`` from src to dst, then delete from src.

    Used when (un)publishing a document moves it between the private and public buckets.
    """

    def _move() -> None:
        s3 = _s3()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=src_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                s3.copy_object(Bucket=dst_bucket, Key=key, CopySource={"Bucket": src_bucket, "Key": key})
                s3.delete_object(Bucket=src_bucket, Key=key)

    await asyncio.to_thread(_move)


async def delete_prefix(bucket: str, prefix: str) -> None:
    def _delete() -> None:
        s3 = _s3()
        keys: list[dict] = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys.extend({"Key": o["Key"]} for o in page.get("Contents", []))
        for i in range(0, len(keys), 1000):
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys[i : i + 1000]})

    await asyncio.to_thread(_delete)
