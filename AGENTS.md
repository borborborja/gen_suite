# gen_suite — guía para agentes de código

> `CLAUDE.md` es un symlink a este fichero (igual en `backend/` y `frontend/`).
> Edita siempre `AGENTS.md`; hay guías anidadas con detalle por área en
> `backend/AGENTS.md` y `frontend/AGENTS.md`.

Suite de investigación genealógica **multi-tenant** (Docker). El árbol GEDCOM es la columna
vertebral; alrededor: transcripción de manuscritos (OCR/HTR), documentos, búsqueda (pgvector),
extracción de actas, linkage y conector FamilySearch. **Docs, commits y strings de usuario en
español**; identificadores de código y la mayoría de comentarios en inglés.

Stack: FastAPI + SQLAlchemy async + ARQ · Postgres 16 + pgvector (RLS) · Redis · MinIO/S3 ·
React 18 + Vite + TypeScript.

## Comandos

```bash
# Stack completo (db, redis, minio, api, worker, frontend)
docker compose --env-file config/.env up -d --build

# Backend dev (Python 3.12; necesita gen-suite-db y gen-suite-redis arriba)
cd backend && pip install -e ".[dev]"
POSTGRES_HOST=localhost alembic upgrade head
POSTGRES_HOST=localhost uvicorn app.main:app --reload          # API :8000
POSTGRES_HOST=localhost arq app.tasks.worker.WorkerSettings    # worker

# Frontend
cd frontend && npm install && npm run dev    # :5173, proxy /api → :8000
npm run typecheck                            # tsc --noEmit (no hay linter)

# Tests (pytest; requieren Postgres+Redis vivos)
docker compose run --rm gen-suite-backend pytest -q
docker compose run --rm gen-suite-backend pytest tests/test_tree.py -q      # un fichero
docker compose run --rm gen-suite-backend pytest -k "nombre_del_test" -q    # un test
```

⚠️ **Los tests TRUNCAN** `memberships, refresh_tokens, tenants, users` de la BD conectada
antes de cada test (`backend/tests/conftest.py`). Nunca los apuntes a una BD con datos reales.

## Arquitectura — lo que hay que saber para no romper nada

### Aislamiento multi-tenant = Row-Level Security de Postgres (invariante central)

El aislamiento lo impone la **base de datos**, no el código. Dos roles de BD: el admin
(`POSTGRES_USER`) solo migra; app y worker conectan como **no-superusuario** (`APP_DB_USER`)
para que la RLS aplique. Las políticas usan GUCs transaction-local (`app.user_id`,
`app.tenant_id`, `app.user_role`, `app.is_server_admin`) fijados en `backend/app/db/rls.py`.

Consecuencias obligatorias:

- Los GUCs se fijan con `set_config(..., is_local=true)` → **cualquier commit pierde el
  contexto RLS en silencio** (las queries siguientes ven 0 filas). Los endpoints NO hacen
  commit a mitad de request: la dependencia de sesión hace el único commit al final. En tasks
  del worker que comiten entre pasos, usa `commit_keep_rls()` (`app/db/rls.py`), nunca
  `commit()` a pelo seguido de más queries.
- Tres dependencias de sesión en `backend/app/core/deps.py`: `get_db` (sin RLS — solo
  login/register), `get_authn_db` (contexto de usuario), `get_tenant_db` (contexto completo
  de tenant — la habitual en módulos de tenant).
- Toda tabla tenant-scoped nueva necesita su **política RLS en la migración** que la crea.
- `tests/test_rls_isolation.py` verifica el aislamiento a nivel HTTP y BD; debe seguir en verde.

### Monolito modular (backend) y jobs en background

- Cada módulo en `backend/app/modules/<nombre>/` con `router.py` (exporta `router`),
  `schemas.py`, `service.py`; se registra en `MODULE_ROUTERS` de `app/main.py` (montado en `/api`).
- El trabajo largo (transcripción, extracción, embeddings…) **nunca corre inline en la API**:
  se crea un `Job` y se encola una task ARQ (`backend/app/tasks/*_tasks.py`). Toda task nueva
  **debe añadirse a `WorkerSettings.functions`** en `app/tasks/worker.py` o no correrá.
  Progreso vía Redis pub/sub → SSE (`GET /api/jobs/{id}/events`); cancelación cooperativa.
- Los binarios (S3/MinIO) se sirven **solo a través del backend**; MinIO nunca se expone.
- Detalle completo en `backend/AGENTS.md`.

### Frontend

React 18 + Vite, superficie de dependencias mínima (react + d3-selection/d3-zoom) — no añadir
librerías (router, estado, UI kits) sin preguntar. Detalle en `frontend/AGENTS.md`.

### Config y despliegue

Toda la config en `config/.env` (desde `config/.env.example`); datos persistentes en `./data`.
`compose.yaml` es el stack portable; `compose.micapum.yaml` (git-ignored) es el overlay privado
del homelab — no referenciarlo en código portable. Detalles en `DEPLOY.md`.

## Convenciones

- Commits: `tipo: descripción` (feat, fix, refactor, docs, test, chore), mensaje en español.
- Migraciones en `backend/alembic/versions/` con prefijo numérico secuencial (`0027_...`),
  escritas a mano (no autogenerate).
- Mensajes de error visibles para el usuario en español.
- Trabaja en ramas, no directamente en `main`.

## Límites

**Siempre:**
- Ejecuta los tests (backend) y `npm run typecheck` (frontend) tras cambios; muestra la salida.
- Cambios pequeños y revisables que imiten el estilo del código circundante.
- Tabla tenant-scoped nueva → política RLS en su migración + caso en `test_rls_isolation.py`.

**Pregunta antes:**
- Añadir dependencias (backend o frontend).
- `git push`, merges a `main`, o crear/borrar ramas remotas.
- Cualquier `docker compose down -v`, borrado de volúmenes o comandos que toquen `./data`.
- Cambios de esquema que requieran migración de datos existentes.

**Nunca:**
- Leer o imprimir `config/.env`, claves o secretos (usa `config/.env.example` como referencia).
- Editar migraciones alembic ya existentes — crea siempre una nueva.
- Ejecutar los tests contra una BD con datos reales (truncan tablas).
- Exponer MinIO/Postgres/Redis fuera del stack, ni servir binarios saltándose el backend.
- Comandos destructivos (`rm -rf`, `git push --force`) sin petición explícita del usuario.
