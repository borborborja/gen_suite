"""One reserved S3 attempt per object, bounded multipart reads, no SDK retries."""
from __future__ import annotations
import asyncio
import os
from botocore.exceptions import ClientError
from fastapi import HTTPException
from ..modules.storage_audit import inventory, quota

CHUNK = 16 * 1024**2
MAX_CHUNK = 256 * 1024**2


def part_size(size):
    value = max(CHUNK, (size + 9999) // 10000)
    if value > MAX_CHUNK:
        raise HTTPException(413, "El archivo supera el tamaño multipart admitido con memoria acotada.")
    return value


async def checked_head(client, write, expected_size, version_id=None):
    options = {"VersionId": version_id} if version_id is not None else {}
    head = await asyncio.to_thread(client.head_object, Bucket=write.bucket, Key=write.storage_key, **options)
    if head.get("ContentLength") != expected_size or head.get("Metadata", {}).get("gensuite-write-id") != str(write.id):
        raise ValueError("El almacenamiento no acredita el tamaño e identidad de esta escritura")
    version = head.get("VersionId", "null")
    inventory.opaque(version, 1024)
    return version


async def execute(client, bucket, key, size, kind, transport, *, write_id=None, target_version_id=None):
    if write_id is None:
        write = await quota.reserve_independent(bucket, key, size, kind=kind, target_version_id=target_version_id)
        write_id = write.id
    write = await quota.begin(bucket, key, size, write_id)
    try:
        if write.kind != kind or write.target_version_id != target_version_id:
            raise ValueError("La reserva no corresponde a esta operación")
        response = await transport(write)
        if kind in {"delete", "delete_version"}:
            # No credit based on a delete acknowledgement (it may only create a marker).
            version = response.get("VersionId")
        else:
            version = await checked_head(client, write, size, response.get("VersionId"))
        await quota.finish(write, size, version)
    except BaseException:
        try: await asyncio.shield(quota.unknown(write))
        except Exception: pass  # The durable 'sending' reservation remains charged.
        raise


async def put(client, bucket, key, data, content_type, *, write_id=None):
    async def send(write):
        return await asyncio.to_thread(client.put_object, Bucket=bucket, Key=key, Body=data,
            ContentType=content_type, Metadata={"gensuite-write-id": str(write.id)})
    await execute(client, bucket, key, len(data), "put", send, write_id=write_id)


async def multipart(client, write, size, content_type, read_part, *, metadata=None):
    stride = part_size(size)
    created = await asyncio.to_thread(client.create_multipart_upload, Bucket=write.bucket, Key=write.storage_key,
        ContentType=content_type, Metadata={**(metadata or {}), "gensuite-write-id": str(write.id)})
    await quota.record_upload(write, created["UploadId"])
    parts = []  # At most 10,000 small receipts, never all source bytes.
    for offset in range(0, size, stride):
        number = len(parts) + 1; length = min(stride, size - offset)
        response = await read_part(write, offset, length, number)
        tag = response.get("ETag")
        if not isinstance(tag, str) or not tag: raise ValueError("Falta el recibo de una parte")
        parts.append({"PartNumber": number, "ETag": tag})
    return await asyncio.to_thread(client.complete_multipart_upload, Bucket=write.bucket, Key=write.storage_key,
        UploadId=write.upload_id, MultipartUpload={"Parts": parts})


async def upload(client, bucket, key, path, content_type):
    # Pin the descriptor and byte length before reserving. Every part read is capped;
    # a file growing afterwards cannot send more than the reserved amount to S3.
    with open(path, "rb") as source:
        size = os.fstat(source.fileno()).st_size
        part_size(size)
        async def send(write):
            if size <= CHUNK:
                data = await asyncio.to_thread(source.read, size)
                if len(data) != size: raise ValueError("El archivo local cambió antes de subirlo")
                return await asyncio.to_thread(client.put_object, Bucket=bucket, Key=key, Body=data,
                    ContentType=content_type, Metadata={"gensuite-write-id": str(write.id)})
            async def part(write, offset, length, number):
                data = await asyncio.to_thread(source.read, length)
                if len(data) != length: raise ValueError("El archivo local cambió durante la subida")
                return await asyncio.to_thread(client.upload_part, Bucket=bucket, Key=key,
                    UploadId=write.upload_id, PartNumber=number, Body=data)
            return await multipart(client, write, size, content_type, part)
        await execute(client, bucket, key, size, "upload", send)


async def copy(client, src_bucket, dst_bucket, key):
    quota.scope(src_bucket, key); quota.scope(dst_bucket, key)
    head = await asyncio.to_thread(client.head_object, Bucket=src_bucket, Key=key)
    size = inventory.byte_size(head.get("ContentLength")); part_size(size)
    tag = head.get("ETag")
    if not isinstance(tag, str) or not tag: raise ValueError("El origen de la copia no tiene ETag verificable")
    source = {"Bucket": src_bucket, "Key": key}
    if "VersionId" in head: source["VersionId"] = inventory.opaque(head["VersionId"], 1024)
    small_data = b""
    if 0 < size <= CHUNK:
        # S3 does not support UploadPartCopy ranges for sources <=5 MB. A bounded
        # read before admission avoids both that limitation and an unbounded copy of
        # a concurrently growing unversioned source. No S3 mutation has started yet.
        obj = await asyncio.to_thread(client.get_object, Bucket=src_bucket, Key=key, IfMatch=tag,
            Range=f"bytes=0-{size - 1}", **({"VersionId": source["VersionId"]} if "VersionId" in source else {}))
        try:
            if obj.get("ContentLength") != size: raise ValueError("El origen de la copia cambió de tamaño")
            small_data = await asyncio.to_thread(obj["Body"].read, size + 1)
            if len(small_data) != size: raise ValueError("El origen no proporciona los bytes esperados")
        finally:
            await asyncio.to_thread(obj["Body"].close)
    async def send(write):
        metadata = {**head.get("Metadata", {}), "gensuite-write-id": str(write.id)}
        if size <= CHUNK:
            return await asyncio.to_thread(client.put_object, Bucket=dst_bucket, Key=key, Body=small_data,
                Metadata=metadata, ContentType=head.get("ContentType", "application/octet-stream"))
        # Explicit ranges bound every server-side copy to the reserved byte length.
        async def part(write, offset, length, number):
            response = await asyncio.to_thread(client.upload_part_copy, Bucket=dst_bucket, Key=key,
                UploadId=write.upload_id, PartNumber=number, CopySource=source,
                CopySourceIfMatch=tag, CopySourceRange=f"bytes={offset}-{offset + length - 1}")
            return response.get("CopyPartResult", {})
        return await multipart(client, write, size, head.get("ContentType", "application/octet-stream"), part, metadata=metadata)
    await execute(client, dst_bucket, key, size, "copy", send)


async def delete(client, bucket, key, *, version_id=None):
    if version_id is not None: inventory.opaque(version_id, 1024)
    async def send(write):
        try:
            return await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=key,
                **({"VersionId": version_id} if version_id is not None else {}))
        except ClientError as error:
            if version_id is None or error.response.get("Error", {}).get("Code") not in {"NoSuchKey", "NoSuchVersion"}: raise
            return {}
    await execute(client, bucket, key, 0, "delete_version" if version_id is not None else "delete", send, target_version_id=version_id)
