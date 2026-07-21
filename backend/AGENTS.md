# backend — guía para agentes

FastAPI (monolito modular) + SQLAlchemy 2 async + ARQ. Python 3.12. Lee primero el
`AGENTS.md` raíz: la sección de RLS es de cumplimiento obligatorio.

## Comandos

```bash
pip install -e ".[dev]"                                        # instalar (o uv pip install -e ".[dev]")
POSTGRES_HOST=localhost alembic upgrade head                   # migrar
POSTGRES_HOST=localhost uvicorn app.main:app --reload          # API :8000 (Swagger en /docs)
POSTGRES_HOST=localhost arq app.tasks.worker.WorkerSettings    # worker (o ./run_worker.sh)

# Tests — requieren Postgres+Redis vivos; TRUNCAN tablas de identidad (ver conftest.py)
docker compose run --rm gen-suite-backend pytest -q
docker compose run --rm gen-suite-backend pytest tests/test_documents.py -q
docker compose run --rm gen-suite-backend pytest -k "nombre" -q
```

`ENVIRONMENT=development` desactiva la validación fail-fast de secretos de `app/settings.py`.

## Receta: endpoint o módulo nuevo

1. Módulo en `app/modules/<nombre>/` con `router.py` (exporta `router` con prefijo y tags),
   `schemas.py` (Pydantic), `service.py` (lógica). Mira `app/modules/documents/` como ejemplo.
2. Registra el router en `MODULE_ROUTERS` (`app/main.py`).
3. Sesión: `get_tenant_db` para todo lo tenant-scoped; `get_authn_db` si no hay tenant activo;
   `get_db` solo para auth pública. **No hagas `session.commit()` en el endpoint** — la
   dependencia comitea al final (un commit intermedio pierde el contexto RLS).
4. Errores de usuario: lanza `AppError` (`app/core/errors.py`) con mensaje en español.
5. Test en `tests/test_<nombre>.py` siguiendo el estilo de los existentes (httpx AsyncClient
   contra la app ASGI; fixtures en `conftest.py`).

## Receta: tabla nueva

1. Modelo en `app/models/<nombre>.py` (un fichero por modelo), exportado en `app/models/__init__.py`.
2. Migración manual numerada en `alembic/versions/` (siguiente `00NN_`). Si la tabla es
   tenant-scoped: columna `tenant_id`, `ENABLE ROW LEVEL SECURITY` y política keyed en los
   GUCs `app.*` — copia el patrón de cualquier migración existente (p.ej. `0008_records_extraction.py`).
3. Añade cobertura de aislamiento en `tests/test_rls_isolation.py`.

## Receta: task de background nueva

1. Función async en `app/tasks/<área>_tasks.py`; firma `(ctx, job_id, ...)`.
2. **Añádela a `WorkerSettings.functions`** en `app/tasks/worker.py` (si no, nunca corre).
3. Crea el `Job` y encola desde el servicio con `app/core/queue.py`; publica progreso con
   `app/core/events.py` (Redis → SSE en `/api/jobs/{id}/events`).
4. Cancelación cooperativa: consulta el estado del job periódicamente y aborta limpio.
5. Si comiteas entre pasos, re-aplica el contexto con `commit_keep_rls()` (`app/db/rls.py`).
6. Jobs largos: revisa `job_timeout` en `worker.py` (el default de arq son 300 s).

## Otras notas

- Motores OCR en `app/modules/transcription/ocr_engines.py`; la selección de
  proveedor/modelo/credencial pasa por el módulo `providers`.
- API keys externas: prefijo `gsk_`, mismo header `Authorization: Bearer`, se resuelven en
  `app/core/deps.py` contra la tabla `api_keys`.
- Secretos de conectores/proveedores se cifran con `SUITE_MASTER_KEY` (`app/core/crypto.py`).
- Storage S3 solo vía `app/core/storage.py`; nunca URLs presignadas al cliente.
- Conectores: se auto-registran en `app/connectors/registry.py`; solo los habilitados por env
  se montan bajo `/api/connectors`.
