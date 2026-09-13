"""Transfer a verified recovery set to/from the separately configured backup store."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from .archive import BLOCK, FORMATS, ROLES, RecoveryError, canonical, descriptor, lines, object_path, private_file, verify
from .service import new_directory


def prefix(bundle_id):
    return f"_backups/full/{uuid.UUID(str(bundle_id))}/"


def read_hash(response, expected_size):
    checksum, size = hashlib.sha256(), 0
    try:
        while chunk := response["Body"].read(BLOCK):
            checksum.update(chunk); size += len(chunk)
            if size > expected_size:
                raise RecoveryError("La copia remota supera su tamaño esperado.")
    finally:
        response["Body"].close()
    return {"sha256": checksum.hexdigest(), "size": size}


def upload(s3, bucket, root):
    from boto3.s3.transfer import TransferConfig
    root = Path(root)
    manifest = verify(root)
    bundle = uuid.uuid4()
    base = prefix(bundle)
    if s3.list_objects_v2(Bucket=bucket, Prefix=base, MaxKeys=1).get("Contents"):
        raise RecoveryError("El destino remoto ya existe; no se sobrescribe.")
    config = TransferConfig(max_concurrency=1, use_threads=False)

    def send(path, name, expected):
        s3.upload_file(str(path), bucket, base + name, ExtraArgs={"ContentType": "application/octet-stream"}, Config=config)
        if read_hash(s3.get_object(Bucket=bucket, Key=base + name), expected["size"]) != expected:
            raise RecoveryError("La lectura de la copia remota no coincide; no se publica el manifiesto final.")

    for name, expected in manifest["files"].items():
        send(root / name, name, expected)
    for row in lines(root / "objects.jsonl"):
        path = object_path(root, row["role"], row["key"])
        send(path, path.relative_to(root).as_posix(), {"sha256": row["sha256"], "size": row["size"]})
    send(root / "manifest.json", "manifest.json", descriptor(root / "manifest.json"))
    return {"bundle_id": str(bundle), "backup_id": manifest["id"], "remote_readback_verified": True,
            "restoration_verified": False}


def download(s3, bucket, bundle_id, path):
    base = prefix(bundle_id)
    response = s3.get_object(Bucket=bucket, Key=base + "manifest.json")
    data = bytearray()
    try:
        while chunk := response["Body"].read(BLOCK):
            data.extend(chunk)
            if len(data) > 8 * BLOCK: raise RecoveryError("Manifiesto remoto demasiado grande.")
    finally:
        response["Body"].close()
    manifest = json.loads(data)
    if (manifest.get("format") not in FORMATS or manifest.get("complete") is not True
            or set(manifest.get("files", {})) != {"database.dump", "objects.jsonl", "references.jsonl"}):
        raise RecoveryError("La copia remota no tiene un manifiesto completo reconocido.")
    root = new_directory(path)
    (root / "objects").mkdir(mode=0o700)
    for role in ROLES: (root / "objects" / role).mkdir(mode=0o700)

    def receive(name, target, expected):
        if type(expected.get("size")) is not int or expected["size"] < 0:
            raise RecoveryError("Tamaño remoto no válido.")
        response = s3.get_object(Bucket=bucket, Key=base + name)
        checksum, size = hashlib.sha256(), 0
        try:
            with private_file(target) as stream:
                while chunk := response["Body"].read(BLOCK):
                    size += len(chunk)
                    if size > expected["size"]: raise RecoveryError("El archivo remoto supera su tamaño declarado.")
                    stream.write(chunk); checksum.update(chunk)
        finally:
            response["Body"].close()
        if {"size": size, "sha256": checksum.hexdigest()} != expected:
            raise RecoveryError("El archivo remoto no coincide con el manifiesto.")

    for name, expected in manifest["files"].items(): receive(name, root / name, expected)
    for row in lines(root / "objects.jsonl"):
        target = object_path(root, row["role"], row["key"])
        receive(target.relative_to(root).as_posix(), target, {"size": row["size"], "sha256": row["sha256"]})
    verify(root, manifest=manifest)
    with private_file(root / "manifest.json") as stream: stream.write(canonical(manifest) + b"\n")
    return {"backup_id": manifest["id"], "integrity_verified": True, "restoration_verified": False}


def require_separate_store(settings):
    if (not settings.backup_s3_bucket or not settings.backup_s3_endpoint or not settings.backup_s3_access_key or not settings.backup_s3_secret_key
            or settings.backup_s3_bucket in (settings.minio_bucket_private, settings.minio_bucket_public)
            or settings.backup_s3_access_key == settings.minio_access_key):
        raise RecoveryError("Configura un bucket y credencial de backup separados del almacenamiento de la aplicación.")
