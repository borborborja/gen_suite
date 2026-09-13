"""python -m app.recovery: no services are started and no source data are deleted."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .archive import RecoveryError, verify


def parser():
    result = argparse.ArgumentParser(description="Recuperación completa de una instalación detenida. Solo operadores; nunca copias de terceros no confiables.")
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Copiar PostgreSQL y los dos buckets a un directorio NUEVO")
    create.add_argument("directory")
    create.add_argument("--confirm-quiesced", action="store_true", help="API, workers, importadores y demás escritores están detenidos durante toda la copia")
    create.add_argument("--confirm-key-escrow", action="store_true", help="La configuración y SUITE_MASTER_KEY están custodiadas por separado")
    check = commands.add_parser("verify", help="Verificar bytes y referencias sin red ni configuración")
    check.add_argument("directory")
    upload = commands.add_parser("upload", help="Subir una copia verificada al S3 de backup separado y comprobar su lectura")
    upload.add_argument("directory")
    download = commands.add_parser("download", help="Descargar un bundle remoto a un directorio NUEVO y verificarlo")
    download.add_argument("bundle_id")
    download.add_argument("directory")
    restore = commands.add_parser("restore", help="Restaurar solo en otro clúster y buckets vacíos gensuite-restore-*")
    restore.add_argument("directory")
    restore.add_argument("--confirm-empty-isolated", action="store_true")
    restore.add_argument("--trust-backup", action="store_true", help="El dump ejecuta SQL: procede de una fuente propia y confiable")
    restore.add_argument("--confirm-key-escrow", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            manifest = verify(args.directory)
            result = {"id": manifest["id"], "integrity_verified": True, "objects": manifest["objects"],
                      "references": manifest["references"], "restoration_verified": False}
        else:
            from ..settings import settings
            from ..core.storage import _s3, backup_s3
            from .service import create, restore
            if args.command in ("upload", "download"):
                from .remote import download, upload, require_separate_store
                require_separate_store(settings)
                result = (upload(backup_s3(), settings.backup_s3_bucket, args.directory) if args.command == "upload"
                          else download(backup_s3(), settings.backup_s3_bucket, args.bundle_id, args.directory))
            elif args.command == "create":
                manifest = asyncio.run(create(settings, _s3(), args.directory,
                    quiesced=args.confirm_quiesced, key_escrow=args.confirm_key_escrow))
                result = {"id": manifest["id"], "complete": True, "objects": manifest["objects"], "restoration_verified": False}
            else:
                result = asyncio.run(restore(settings, _s3(), args.directory, isolated=args.confirm_empty_isolated,
                    trusted=args.trust_backup, key_escrow=args.confirm_key_escrow))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except RecoveryError as error:
        print(f"Recuperación detenida: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrumpido. Se conservan los archivos y cualquier destino parcial; no se da por completado.", file=sys.stderr)
    except Exception:
        # Storage keys, SQL errors, endpoints and subprocess output can contain private data.
        print("Recuperación fallida. Comprueba configuración, permisos, espacio y disponibilidad. No se eliminan datos ni se da el proceso por completado.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
