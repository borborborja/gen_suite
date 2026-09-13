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
- **Auth**: propia, JWT (access+refresh) + Argon2id; roles de espacio `owner`, `admin`,
  `editor`, `contributor`, `viewer` y `guest`.

Dos roles de Postgres: el *admin* (`POSTGRES_USER`) solo migra; la app y el worker se
conectan con un rol **no-superusuario** (`APP_DB_USER`) para que la RLS se aplique.

## Instalar y arrancar con Docker Compose

La instalación principal es **Docker Compose 2.20+**. No requiere `install.sh`, Python ni Node
en el servidor; las dependencias de la aplicación están en los contenedores.

Solo en una instalación nueva, prepara la configuración y completa secretos, URL HTTPS y SMTP
según [DEPLOY.md](DEPLOY.md). Si ya tienes `config/.env`, consérvalo sin regenerar contraseñas:

```bash
cp -n config/.env.example config/.env
chmod 600 config/.env
```

Con la configuración completa, arranca todo:

```bash
docker compose --env-file config/.env up -d --build --wait --wait-timeout 180
```

Compose construye las imágenes, espera a los servicios de datos y ejecuta las migraciones
antes de iniciar API y worker. MinIO viene incluido; también puedes usar S3 externo.

La primera vez, crea la administración desde una terminal (contraseña oculta, sin abrir el registro):

```bash
docker compose --env-file config/.env run --rm --no-deps gen-suite-backend python -m app.bootstrap_admin
```

El comando conserva la administración existente. Para consultar el estado:
`docker compose --env-file config/.env ps --all`.

Conecta tu proxy o túnel HTTPS al frontend en `127.0.0.1:8080` (por defecto). TLS es necesario
para las cookies de sesión seguras. El proxy, SMTP y los backups externos se configuran aparte.

Para desplegar imágenes publicadas sin construir, usa `compose.release.yaml` y fija
`GEN_SUITE_VERSION` en `config/.env`; comandos de instalación y actualización en [DEPLOY.md](DEPLOY.md).

Toda la configuración está en `./config`; los datos, en `./data` por defecto. Custodia `config/.env`
y su clave maestra por separado, con protección de secretos. El asistente `install.sh` se conserva como ayuda **opcional** (requiere
Python 3.10+); Compose no depende de él ni de `config/installer.json`.

### Almacenamiento (biblioteca → MinIO bundled o S3 externo)
La biblioteca (PDFs, imágenes de página, fotos) va a **almacenamiento de objetos**; el texto OCR, actas,
menciones y embeddings van a **Postgres** (consultables, pgvector). Por defecto se usa un **MinIO bundled**
(datos en `./data/minio`) para evaluación/uso privado; no está aprobado para exposición pública por
el [aviso de seguridad pendiente](docs/ESTADO_VALIDACION_REAL.md#minio-multipart-corregido-bloqueo-de-seguridad-pendiente).
Para usar un **S3 externo** mantenido (AWS/Backblaze/Wasabi): en `config/.env` pon
`COMPOSE_PROFILES=` (vacío) y rellena `MINIO_ENDPOINT/SECURE/REGION/ACCESS_KEY/SECRET_KEY` + los 2 buckets
(pre-creados) — el MinIO local ya no arranca. **Backups**: `BACKUP_TO_S3=true` hace un dump diario
solo de PostgreSQL. Para proteger también las imágenes/PDF utiliza la [copia conjunta](docs/RECUPERACION_COMPLETA.md).
Detalles de configuración en `config/.env.example`.

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

Pruebas de configuración Compose y del asistente sin tocar los datos del despliegue:

```bash
python3 -m unittest discover -s scripts/tests -v
```

Las pruebas del backend (`pytest -q` desde `backend/`) deben ejecutarse **solo contra una base
de datos desechable**: sus fixtures borran datos. La CI prepara Postgres, Redis y MinIO aislados.
Cubren auth (registro/login/rotación de refresh) y, sobre todo, **aislamiento RLS**:
`tests/test_rls_isolation.py` prueba a nivel HTTP y a nivel BD que un tenant nunca ve filas
de otro.

## Módulo Árbol (Fase 1)

Columna vertebral: import/export GEDCOM (parser propio, sin dependencias, con detección de
codificación UTF-8/UTF-16/ANSI/ANSEL y round-trip de etiquetas no mapeadas vía `raw`).

- `POST /api/tree/import/gedcom` — sube un `.ged` 5.5/5.5.1 → árbol, fuentes, repositorios y citas.
- `GET /api/tree/stats` · `GET /api/tree/roots` · `GET /api/tree/persons/search?q=`
- `GET /api/tree/persons/{id}` — detalle (nombres, eventos, padres/cónyuges/hijos).
- `GET /api/tree/persons/{id}/subtree?depth=3` — subgrafo ego-céntrico para el visor.
- `GET /api/tree/export/gedcom` — descarga GEDCOM 5.5.1 reconstruido.
- `GET /api/tree/export/gedcom/audit` — revisión de pérdidas y huellas del archivo/informe.
  El botón de exportación exige revisar esta información; no incluye JPG/PDF ni sustituye
  una copia de seguridad. [Cobertura y límites del intercambio](docs/INTERCAMBIO_GEDCOM.md).
- `POST /api/tree/exports` — generación privada en segundo plano de GEDCOM 5.5.1,
  GEDCOM 7.0 y GEDZIP con originales propios. Incluye informe inmutable, cancelación
  y descarga Range; requiere esquema `0052`. [Uso y límites](docs/EXPORTACION_ARBOL.md).
- **Importación revisada GEDCOM 7/GEDZIP**: original privado, informe previo,
  incorporación explícita y atómica, documentos vinculados a fuentes sin fusión ni IA.
  Requiere el head actual `0053`. [Perfil y límites](docs/IMPORTACION_ARBOL.md).

Visor: React + d3-zoom, layout por generaciones, click para re-enfocar, búsqueda y panel de
persona. Todo tenant-scoped por RLS.

## Investigación, colaboración y compartición

Las migraciones `0029`–`0034` introducen la capa evidence-first: fuentes, repositorios,
afirmaciones, citas con transcripción/localizador, notas, etiquetas y proyectos/tareas. Las
personas colaboradoras pueden proponer cambios para revisión; editores trabajan directamente.
Las publicaciones son instantáneas **no listadas**, con enlace secreto, fecha de caducidad y
redacción conservadora: una persona solo se publica si se ha marcado `deceased` o `public`.

La sincronización directa con Gramps Desktop y Gramps Web usa conexiones, una línea base
explícita, envelopes del modelo completo y una safata manual de conflictos/tombstones. El
contrato para la Companion está en [docs/GRAMPS_SYNC.md](docs/GRAMPS_SYNC.md).

**Investigación automática** reúne libros y etapas en una ejecución persistente: preparar
sin gasto, revisar modelos y presupuesto, iniciar, pausar y reanudar conservando resultados.
Las coincidencias no modifican el árbol sin revisión humana. El flujo, migración `0043`
y límites de validación están en [Investigaciones automáticas](docs/INVESTIGACIONES_AUTOMATICAS.md).

La búsqueda vectorial usa [índices por generaciones](docs/INDICES_GENERACIONALES.md):
reconstruir no vacía el corpus, la activación es explícita y las consultas usan el modelo
del índice. La migración `0044` conserva los vectores históricos sin atribuirles un modelo;
hay que crear y activar una generación para utilizar el nuevo flujo semántico.

La migración `0045` añade [autorizaciones económicas versionadas](docs/RENOVACION_AUTORIZACIONES_IA.md):
renovar tarifas caducadas con comparación y confirmación, conservando perfiles, resultados
y costes anteriores. No aumenta presupuestos ni reanuda trabajos automáticamente.

`0046` añade [limpieza explícita de importaciones](docs/LIMPIEZA_IMPORTACIONES.md): localizar
cargas inactivas, confirmar la retirada de temporales y reanudar una limpieza interrumpida.
No borra por antigüedad ni elimina los originales conservados.

`0047` añade [inventarios de almacenamiento](docs/INVENTARIO_ALMACENAMIENTO.md) en Ajustes:
versiones, copias, objetos antiguos sin referencia y partes multipart, con historial y
reanudación. Solo observa metadatos; no borra objetos ni activa una cuota por sí mismo.

`0048` añade [reservas de almacenamiento y recuento protegido](docs/CUOTA_ALMACENAMIENTO.md):
admisión de originales, derivados y copias, más espacio para la respuesta antes de pagar IA.
Los datos existentes exigen un recuento con pausa de nuevas escrituras. Conserva reservas
inciertas y no concede espacio por un simple ACK de borrado. Es una cuota de contenido,
no de disco físico; siguen pendientes validación real y resolución de algunos recibos huérfanos.

`0049` + `0050` añaden [cancelación auditada de reservas no iniciadas](docs/CANCELACION_RESERVAS.md):
revisión y confirmación desde Ajustes, protección de respuestas IA en ejecución y auditoría
sin edición/borrado por la aplicación. No cancela escrituras ya enviadas ni presume su ausencia.

El perfil opcional [PDF/A-2b](docs/PDFA.md) añade conversión local con OCRmyPDF y
validación independiente con veraPDF, sin repetir OCR/IA ni alterar originales.
Se activa con Compose y conserva el informe ligado al checksum del derivado.

La [recuperación de instalación completa](docs/RECUPERACION_COMPLETA.md) añade copia
conjunta PostgreSQL+objetos, verificación, transferencia a S3 de backup y restauración
aislada con cuarentena. Mantiene Compose y no sustituye la futura exportación portable
por espacio. El cron de backup anterior sigue siendo solo PostgreSQL.

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

La IA alojada requiere una credencial guardada, un presupuesto mensual explícito y una
tarifa/límites confirmados por modelo en **Ajustes**. Las reservas se guardan antes de cada
llamada; los resultados inciertos se concilian y no se reintentan automáticamente.
Los nuevos envíos Batch están temporalmente desactivados. Consulta
[Control de gasto de IA](docs/CONTROL_GASTO_IA.md) y el
[estado real del plan](docs/IMPLEMENTACION_INVESTIGACION.md), incluidos los pendientes de validación.

Motores de visión OCR propios en `app/modules/transcription/ocr_engines.py`: tesseract, ollama,
claude, openai, openrouter, google. La orquestación es de la suite: la API encola un job ARQ, el
worker corre el OCR (`_ocr_via_anthropic` / `_ocr_via_openai_compat` / tesseract), escribe el texto
por página y publica progreso a Redis → SSE.

- `POST /api/transcription/jobs` `{document_id, engine?, model?, credential_id?, lang?, psm?}`
  — usa el proveedor asignado a `transcription`, una credencial vinculada al perfil o un motor local explícito.
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
