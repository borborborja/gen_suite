"""Bounded S3 metadata reads. Never upload, download object content or delete anything.

One listing is not a multi-request snapshot. These observations must not be used to
release reservations or enforce a storage quota without a separate write fence.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from urllib.parse import unquote
from botocore.exceptions import ClientError
from ...settings import settings

PAGE_SIZE = 100
FALLBACK_UPLOAD_LIMIT = PAGE_SIZE
MAX_BYTES = 2**63 - 1
CATEGORIES = ("documents", "exports", "staging", "media", "ai_responses", "other")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def configuration():
    buckets = [settings.minio_bucket_private, settings.minio_bucket_public]
    if len(set(buckets)) != 2 or not all(isinstance(b, str) and 0 < len(b) <= 255 for b in buckets):
        raise ValueError("El inventario requiere dos buckets de aplicación distintos")
    return buckets, fingerprint({"buckets": buckets, "endpoint": settings.minio_endpoint,
        "secure": settings.minio_secure, "region": settings.minio_region, "style": settings.minio_addressing_style})


def matches_configuration(scan):
    if scan.invalidated_at is not None: return False
    try:
        buckets, digest = configuration()
    except ValueError:
        return False
    return scan.buckets == buckets and scan.location_hash == digest


def tenant_prefix(tenant_id):
    return f"{uuid.UUID(str(tenant_id))}/"


def validate_key(tenant_id, key):
    # S3 keys are opaque names, NOT filesystem paths. Do not normalize '..', '%' or '/'.
    if (not isinstance(key, str) or not key.startswith(tenant_prefix(tenant_id))
            or "\x00" in key or len(key.encode("utf-8")) > 1024):
        raise ValueError("Clave fuera del espacio o no representable de forma segura")
    return key


def decoded(value):
    if not isinstance(value, str) or re.search(r"%(?![a-fA-F0-9]{2})", value):
        raise ValueError("Nombre S3 con codificación no válida")
    # Explicit EncodingType prevents botocore from doing its automatic decode. Exactly
    # one decode preserves literal percent sequences and '+' in archival filenames.
    return unquote(value, encoding="utf-8", errors="strict")


def opaque(value, maximum):
    if not isinstance(value, str) or not 0 < len(value) <= maximum or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Identificador de versión o carga incompleto")
    return value


def byte_size(value):
    if type(value) is not int or not 0 <= value <= MAX_BYTES:
        raise ValueError("Tamaño ausente o no válido")
    return value


def category_for(tenant_id, key):
    suffix = validate_key(tenant_id, key)[len(tenant_prefix(tenant_id)):]
    first, _, rest = suffix.partition("/")
    if first == "ingestion": return "staging"
    if first == "person_media": return "media"
    if first == "ai-calls": return "ai_responses"
    if first == "tree-exports": return "exports"
    if first == "tree-imports": return "staging"
    try:
        if str(uuid.UUID(first)) == first and rest:
            return "exports" if rest.startswith("exports/") else "documents"
    except ValueError:
        pass
    return "other"


def entry(tenant_id, key, kind, **values):
    return {"storage_key": validate_key(tenant_id, key), "kind": kind,
        "category": category_for(tenant_id, key), "version_id": None, "upload_id": None,
        "part_number": None, "byte_size": 0, "is_latest": None, "upload_state": None,
        "parts_cursor": 0, **values}


def item_id(scan_id, slot, row):
    return uuid.uuid5(scan_id, fingerprint({"slot": slot, "kind": row["kind"], "key": row["storage_key"],
        "version": row["version_id"], "upload": row["upload_id"], "part": row["part_number"]}))


def list_header(result, bucket, tenant_id, bucket_field):
    if (not isinstance(result, dict) or result.get(bucket_field) != bucket
            or result.get("EncodingType") != "url" or decoded(result.get("Prefix")) != tenant_prefix(tenant_id)
            or type(result.get("IsTruncated")) is not bool or result.get("CommonPrefixes")):
        raise ValueError("La respuesta no acredita el bucket, prefijo y final de página solicitados")


def array(result, field):
    values = result.get(field, [])
    if not isinstance(values, list) or len(values) > PAGE_SIZE or any(not isinstance(v, dict) for v in values):
        raise ValueError("Página de almacenamiento no válida o demasiado grande")
    return values


def next_cursor(result, cursor, rows, tenant_id, id_field):
    if cursor and (decoded(result.get("KeyMarker")) != cursor["KeyMarker"] or result.get(id_field) != cursor[id_field]):
        raise ValueError("La respuesta no corresponde al cursor solicitado")
    if not result["IsTruncated"]: return {}
    key = validate_key(tenant_id, decoded(result.get("NextKeyMarker")))
    identifier = cursor_identifier(result.get("Next" + id_field), id_field)
    value = {"KeyMarker": key, id_field: identifier}
    if not rows or value == cursor or (cursor and key < cursor["KeyMarker"]):
        raise ValueError("El almacenamiento no avanza su cursor")
    return value


def cursor_identifier(value, id_field):
    # An explicitly empty version marker advances by key in unversioned buckets.
    # Never confuse it with a missing marker or with an object's literal 'null' ID.
    if id_field == "VersionIdMarker" and value == "": return value
    return opaque(value, 1024 if id_field == "VersionIdMarker" else 2048)


def validate_cursor(tenant_id, cursor, id_field):
    if not isinstance(cursor, dict) or (cursor and set(cursor) != {"KeyMarker", id_field}):
        raise ValueError("Cursor de almacenamiento no válido")
    if cursor:
        validate_key(tenant_id, cursor["KeyMarker"])
        cursor_identifier(cursor[id_field], id_field)


def versions_page(client, bucket, tenant_id, cursor):
    validate_cursor(tenant_id, cursor, "VersionIdMarker")
    result = client.list_object_versions(Bucket=bucket, Prefix=tenant_prefix(tenant_id),
        MaxKeys=PAGE_SIZE, EncodingType="url", **cursor)
    list_header(result, bucket, tenant_id, "Name")
    rows = []
    for field, kind in (("Versions", "version"), ("DeleteMarkers", "delete_marker")):
        for value in array(result, field):
            if type(value.get("IsLatest")) is not bool: raise ValueError("Falta la condición de versión vigente")
            rows.append(entry(tenant_id, decoded(value.get("Key")), kind,
                version_id=opaque(value.get("VersionId"), 1024), is_latest=value["IsLatest"],
                byte_size=byte_size(value.get("Size")) if kind == "version" else 0))
    if len(rows) > PAGE_SIZE: raise ValueError("La página supera el límite solicitado")
    return rows, next_cursor(result, cursor, rows, tenant_id, "VersionIdMarker")


def uploads_page(client, bucket, tenant_id, cursor):
    validate_cursor(tenant_id, cursor, "UploadIdMarker")
    result = client.list_multipart_uploads(Bucket=bucket, Prefix=tenant_prefix(tenant_id),
        MaxUploads=PAGE_SIZE, EncodingType="url", **cursor)
    list_header(result, bucket, tenant_id, "Bucket")
    rows = [entry(tenant_id, decoded(v.get("Key")), "upload", upload_id=opaque(v.get("UploadId"), 2048),
        upload_state="pending") for v in array(result, "Uploads")]
    following = next_cursor(result, cursor, rows, tenant_id, "UploadIdMarker")
    if rows or following:
        return rows, following
    # MinIO RELEASE.2025-04-22 returns an empty successful response when Prefix is
    # supplied even though matching multipart uploads exist. A bounded compatibility
    # probe below verifies each candidate against the requested bucket and never
    # persists/returns a foreign key. The normal prefixed S3 path remains primary.
    return uploads_page_without_prefix(client, bucket, tenant_id, cursor)


def uploads_page_without_prefix(client, bucket, tenant_id, cursor):
    if cursor:
        raise ValueError("La ruta alternativa no admite un cursor ambiguo")
    prefix = tenant_prefix(tenant_id)
    # One bounded compatibility probe. Some legacy MinIO releases also ignore the
    # bucket or ordering on this prefix-free endpoint, so never retain a non-tenant
    # key and never accept a page that might require unsafe foreign cursors.
    result = client.list_multipart_uploads(Bucket=bucket, MaxUploads=FALLBACK_UPLOAD_LIMIT,
        EncodingType="url")
    if (not isinstance(result, dict) or result.get("Bucket") != bucket
            or result.get("EncodingType") != "url" or decoded(result.get("Prefix", "")) != ""
            or decoded(result.get("KeyMarker", "")) != ""
            or result.get("UploadIdMarker", "") != ""
            or type(result.get("IsTruncated")) is not bool or result.get("CommonPrefixes")):
        raise ValueError("La respuesta alternativa no acredita el bucket, cursor y final de página solicitados")
    values = result.get("Uploads", [])
    if (not isinstance(values, list) or len(values) >= FALLBACK_UPLOAD_LIMIT
            or result["IsTruncated"] or any(not isinstance(value, dict) for value in values)):
        raise ValueError("La lista alternativa de cargas no es completa y acotada")
    rows, identities = [], set()
    for value in values:
        key = decoded(value.get("Key"))
        if key.startswith(prefix):
            upload_id = opaque(value.get("UploadId"), 2048)
            if (key, upload_id) in identities:
                raise ValueError("El almacenamiento repitió una carga multipart")
            identities.add((key, upload_id))
            try:
                probe = client.list_parts(Bucket=bucket, Key=key, UploadId=upload_id,
                    MaxParts=1, PartNumberMarker=0)
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") == "NoSuchUpload":
                    # The legacy endpoint can leak another bucket's upload. It must
                    # never become a row owned by this bucket/tenant.
                    continue
                raise
            if (not isinstance(probe, dict) or probe.get("Bucket") != bucket
                    or probe.get("Key") != key or probe.get("UploadId") != upload_id
                    or probe.get("PartNumberMarker") != 0
                    or type(probe.get("IsTruncated")) is not bool):
                raise ValueError("La carga multipart alternativa no acredita su bucket")
            rows.append(entry(tenant_id, key, "upload", upload_id=upload_id,
                upload_state="pending"))
    return rows, {}


def parts_page(client, bucket, tenant_id, upload):
    key = validate_key(tenant_id, upload.storage_key)
    upload_id = opaque(upload.upload_id, 2048)
    marker = upload.parts_cursor
    if type(marker) is not int or not 0 <= marker <= 10000: raise ValueError("Cursor de partes no válido")
    result = client.list_parts(Bucket=bucket, Key=key, UploadId=upload_id, MaxParts=PAGE_SIZE, PartNumberMarker=marker)
    if (not isinstance(result, dict) or result.get("Bucket") != bucket or result.get("Key") != key
            or result.get("UploadId") != upload_id or type(result.get("IsTruncated")) is not bool
            or type(result.get("PartNumberMarker")) is not int or result["PartNumberMarker"] != marker):
        raise ValueError("La respuesta no acredita la carga multipart solicitada")
    rows, previous = [], marker
    for value in array(result, "Parts"):
        number = value.get("PartNumber")
        if type(number) is not int or not previous < number <= 10000: raise ValueError("Partes desordenadas o repetidas")
        rows.append(entry(tenant_id, key, "part", upload_id=upload_id, part_number=number, byte_size=byte_size(value.get("Size"))))
        previous = number
    if result["IsTruncated"]:
        following = result.get("NextPartNumberMarker")
        if not rows or type(following) is not int or following != previous or following >= 10000:
            raise ValueError("El cursor de partes no avanza exactamente hasta la última parte")
        return rows, following
    return rows, None
