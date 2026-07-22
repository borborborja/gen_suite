# gen_suite

Suite de investigación genealógica multi-tenant, alojada en Docker. El árbol GEDCOM es la
columna vertebral; alrededor cuelgan módulos de transcripción de manuscritos, búsqueda
(con o sin IA), descarga de FamilySearch (conector bloqueado por `.env`) e inferencia.

Arquitectura completa y fases: ver el plan en
`~/.claude/plans/gen-suite-debe-ser-una-stateful-fog.md`.

## Stack

- **Backend**: FastAPI (monolito modular + registro de conectores), SQLAlchemy async, ARQ.
- **BD**: Postgres 16 + pgvector, **multi-tenant por Row-Level Security**.
- **Storage**: MinIO (S3). **Cola/SSE**: Redis. **Frontend**: React + Vite (nginx en prod).
- **Auth**: propia, JWT (access+refresh) + Argon2id, roles server-admin / tenant-admin /
  researcher / viewer.

Dos roles de Postgres: el *admin* (`POSTGRES_USER`) solo migra; la app y el worker se
conectan con un rol **no-superusuario** (`APP_DB_USER`) para que la RLS se aplique.

## Arrancar (genérico, cualquier host)

```bash
cp config/.env.example config/.env     # rellena secretos (openssl rand -base64 32 → SUITE_MASTER_KEY)
docker compose --env-file config/.env up -d --build
```

- Frontend (UI): `http://localhost:8080`  (cambia `FRONTEND_PORT` en `config/.env`)
- API + Swagger: `http://localhost:8000/docs`  (solo localhost)
- MinIO consola: `http://localhost:9001`  (solo localhost; no la publiques)

`compose.yaml` es **portable** y multi-arquitectura (amd64/arm64 — ver `DEPLOY.md`). Para desplegar
**sin construir**, usando las imágenes publicadas en GHCR por cada release:
`docker compose -f compose.release.yaml --env-file config/.env up -d` (fija la versión con
`GEN_SUITE_VERSION` en `config/.env`; por defecto `latest`). **Toda la config vive
en `./config`** (`config/.env`) y **todos los datos en `./data`** (Postgres/Redis/MinIO; cambia con
`DATA_DIR`) — host-mapeados, visibles y respaldables. El servicio `gen-suite-migrate` aplica
`alembic upgrade head` antes de arrancar. El **primer usuario registrado** queda como `server-admin`
**solo si** `ALLOW_FIRST_USER_ADMIN=true` en `config/.env` (actívalo para el registro inicial y
vuelve a desactivarlo — ver `DEPLOY.md`).

### Almacenamiento (biblioteca → MinIO bundled o S3 externo)
La biblioteca (PDFs, imágenes de página, fotos) va a **almacenamiento de objetos**; el texto OCR, actas,
menciones y embeddings van a **Postgres** (consultables, pgvector). Por defecto se usa un **MinIO bundled**
(datos en `./data/minio`). Para usar un **S3 externo** (AWS/Backblaze/Wasabi): en `config/.env` pon
`COMPOSE_PROFILES=` (vacío) y rellena `MINIO_ENDPOINT/SECURE/REGION/ACCESS_KEY/SECRET_KEY` + los 2 buckets
(pre-creados) — el MinIO local ya no arranca. **Backups**: `BACKUP_TO_S3=true` hace un `pg_dump` diario a
`{bucket}/_backups/` (restaurar con `pg_restore` — ver `DEPLOY.md`). Detalles y ejemplos en `config/.env.example`.

> **Despliegue homelab (privado):** la config con túnel Cloudflare, redes externas e IPs fijas vive en
> `compose.micapum.yaml` (ignorado por git). Úsalo con `docker compose -f compose.micapum.yaml up -d --build`.

## Desarrollo local

```bash
# Backend (requiere Python 3.12)
cd backend && pip install -e ".[dev]"
# con Postgres/Redis arriba (p.ej. docker compose up -d gen-suite-db gen-suite-redis):
POSTGRES_HOST=localhost alembic upgrade head
POSTGRES_HOST=localhost uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxy /api → :8000)
```

## Tests

```bash
docker compose run --rm gen-suite-backend pytest -q
```

Cubre auth (registro/login/rotación de refresh) y, sobre todo, **aislamiento RLS**:
`tests/test_rls_isolation.py` prueba a nivel HTTP y a nivel BD que un tenant nunca ve filas
de otro.

## Módulo Árbol (Fase 1)

Columna vertebral: import/export GEDCOM (parser propio, sin dependencias, con detección de
codificación UTF-8/UTF-16/ANSI/ANSEL y round-trip de etiquetas no mapeadas vía `raw`).

- `POST /api/tree/import/gedcom` — sube un `.ged` (multipart) → personas/familias/eventos/lugares.
- `GET /api/tree/stats` · `GET /api/tree/roots` · `GET /api/tree/persons/search?q=`
- `GET /api/tree/persons/{id}` — detalle (nombres, eventos, padres/cónyuges/hijos).
- `GET /api/tree/persons/{id}/subtree?depth=3` — subgrafo ego-céntrico para el visor.
- `GET /api/tree/export/gedcom` — descarga GEDCOM 5.5.1 reconstruido.

Visor: React + d3-zoom, layout por generaciones, click para re-enfocar, búsqueda y panel de
persona. Todo tenant-scoped por RLS.

## Módulo Documentos (Fase 2)

Almacenaje S3 (MinIO) con acceso **a través del backend** (auth + RLS); MinIO no se expone.

- `POST /api/documents` (multipart: `title`, `visibility`, `rights_declaration`, `files[]`) —
  imágenes → una página cada una; PDF → original + nº de páginas.
- `GET /api/documents?scope=mine|public|all` · `GET /api/documents/{id}` · `/pages`
- `GET /api/documents/{id}/pages/{n}/content` — stream del binario.
- `POST /api/documents/{id}/publish` (declara derechos → bucket público, legible cross-tenant)
  · `/unpublish` · `DELETE`. Los documentos de FamilySearch no pueden publicarse.
- Jobs + SSE: `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` (infra para Fase 4+).

## Módulo Transcripción · M1 (Fase 4)

Motores de visión OCR propios en `app/modules/transcription/ocr_engines.py`: tesseract, ollama,
claude, openai, openrouter, google. La orquestación es de la suite: la API encola un job ARQ, el
worker corre el OCR (`_ocr_via_anthropic` / `_ocr_via_openai_compat` / tesseract), escribe el texto
por página y publica progreso a Redis → SSE.

- `POST /api/transcription/jobs` `{document_id, engine?, model?, credential_id?, lang?, psm?}`
  — usa el proveedor asignado a `transcription` o un override explícito.
- `POST /api/transcription/jobs/{id}/cancel` (cancelación cooperativa) ·
  `GET /api/transcription/documents/{id}` (texto por página).
- Progreso en vivo vía `GET /api/jobs/{id}/events` (SSE). El worker: `arq app.tasks.worker.WorkerSettings`.

## Layout

```
backend/app/{core,db,models,modules,connectors,tasks}   # API modular + RLS + auth
backend/alembic/                                         # migraciones (incl. políticas RLS)
frontend/src/                                            # React (Vite)
libs/                                                    # fs_downloader (FamilySearch, Fase 6)
docker/                                                  # Dockerfiles + nginx
compose.yaml                                             # stack completo (convenciones Micapum)
```
