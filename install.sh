#!/usr/bin/env bash
# Instalación guiada; no instala paquetes del sistema ni requiere pip en el host.
set -euo pipefail
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Necesitas Python 3.10+ y Docker con Compose. No se necesitan paquetes de pip.' >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo 'Actualiza Python a la versión 3.10 o posterior.' >&2
  exit 1
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$project_dir/scripts/install.py" "$@"
