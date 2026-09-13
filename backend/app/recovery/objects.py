"""Bounded S3 transfer with conditional reads, SHA-256 and stable inventory checks."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

from .archive import BLOCK, ROLES, Catalog, RecoveryError, canonical, digest, lines, object_path, private_file


def inventory(s3, buckets):
    for role in ROLES:
        previous = None
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=buckets[role]):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if previous is not None and key <= previous:
                    raise RecoveryError("El almacén no entrega un inventario ordenado y sin duplicados.")
                previous = key
                yield {"role": role, "key": key, "size": obj["Size"], "etag": obj["ETag"],
                       "modified": obj["LastModified"].isoformat()}


def inventory_hash(rows):
    result = hashlib.sha256()
    for row in rows:
        result.update(canonical({key: row[key] for key in ("role", "key", "size", "etag", "modified")}))
        result.update(b"\n")
    return result.hexdigest()


def download_objects(s3, buckets, root):
    root = Path(root)
    (root / "objects").mkdir(mode=0o700)
    for role in ROLES:
        (root / "objects" / role).mkdir(mode=0o700)
    totals = {role: {"count": 0, "bytes": 0} for role in ROLES}
    with private_file(root / "objects.jsonl") as listing:
        for row in inventory(s3, buckets):
            if shutil.disk_usage(root).free < row["size"] + 128 * 1024 ** 2:
                raise RecoveryError("No queda espacio suficiente para el siguiente objeto y el margen de seguridad.")
            response = s3.get_object(Bucket=buckets[row["role"]], Key=row["key"], IfMatch=row["etag"])
            checksum, size = hashlib.sha256(), 0
            try:
                with private_file(object_path(root, row["role"], row["key"])) as target:
                    while chunk := response["Body"].read(BLOCK):
                        size += len(chunk)
                        if size > row["size"]:
                            raise RecoveryError("El objeto supera el tamaño observado en el inventario.")
                        target.write(chunk); checksum.update(chunk)
                if size != row["size"] or response.get("ETag") != row["etag"]:
                    raise RecoveryError("Un objeto cambió durante la copia.")
            finally:
                response["Body"].close()
            row.update(sha256=checksum.hexdigest(), content_type=response.get("ContentType", "application/octet-stream"),
                       metadata=response.get("Metadata", {}), version_id=response.get("VersionId"),
                       headers={key: response[key] for key in ("ContentEncoding", "ContentLanguage", "ContentDisposition", "CacheControl") if key in response})
            listing.write(canonical(row) + b"\n")
            totals[row["role"]]["count"] += 1; totals[row["role"]]["bytes"] += size
    before = inventory_hash(lines(root / "objects.jsonl"))
    if before != inventory_hash(inventory(s3, buckets)):
        raise RecoveryError("El inventario cambió. Mantén detenidos todos los escritores y crea otra copia.")
    return totals, before


def require_empty_buckets(s3, buckets):
    for bucket in buckets.values():
        s3.head_bucket(Bucket=bucket)  # No implicit creation or permission changes.
        if s3.list_objects_v2(Bucket=bucket, MaxKeys=1).get("Contents"):
            raise RecoveryError("Los buckets de restauración deben estar vacíos.")
        # Old versions and outstanding multipart uploads are not an empty target.
        versions = s3.list_object_versions(Bucket=bucket, MaxKeys=1)
        if versions.get("Versions") or versions.get("DeleteMarkers"):
            raise RecoveryError("El destino contiene versiones históricas de objetos.")
        if s3.list_multipart_uploads(Bucket=bucket, MaxUploads=1).get("Uploads"):
            raise RecoveryError("El destino contiene cargas multipart pendientes.")


def upload_objects(s3, buckets, root):
    from boto3.s3.transfer import TransferConfig
    config = TransferConfig(max_concurrency=1, use_threads=False)
    for row in lines(Path(root) / "objects.jsonl"):
        path = object_path(root, row["role"], row["key"])
        # Check again immediately before sending. The input mount must remain read-only.
        if digest(path) != row["sha256"]:
            raise RecoveryError("La copia cambió antes de restaurar un objeto.")
        extras = {"ContentType": row["content_type"], "Metadata": row["metadata"], **row.get("headers", {})}
        if set(extras) - {"ContentType", "Metadata", "ContentEncoding", "ContentLanguage", "ContentDisposition", "CacheControl"}:
            raise RecoveryError("Metadatos de objeto no admitidos.")
        s3.upload_file(str(path), buckets[row["role"]], row["key"], ExtraArgs=extras, Config=config)
        response = s3.get_object(Bucket=buckets[row["role"]], Key=row["key"])
        checksum, size = hashlib.sha256(), 0
        try:
            while chunk := response["Body"].read(BLOCK):
                checksum.update(chunk); size += len(chunk)
                if size > row["size"]:
                    raise RecoveryError("El objeto restaurado supera el tamaño esperado.")
        finally:
            response["Body"].close()
        if checksum.hexdigest() != row["sha256"] or size != row["size"]:
            raise RecoveryError("La lectura del objeto restaurado no coincide con la copia.")
        if (response.get("ContentType", "application/octet-stream") != row["content_type"]
                or response.get("Metadata", {}) != row["metadata"]
                or any(response.get(key) != value for key, value in row.get("headers", {}).items())):
            raise RecoveryError("Los metadatos del objeto restaurado no coinciden con la copia.")
    with Catalog() as catalog:
        expected_count = 0
        for row in lines(Path(root) / "objects.jsonl"):
            catalog.add(row); expected_count += 1
        actual_count = 0
        for row in inventory(s3, buckets):
            catalog.check_reference(row); actual_count += 1
        if actual_count != expected_count:
            raise RecoveryError("El inventario del destino no coincide con la copia.")
