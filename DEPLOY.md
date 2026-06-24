# Despliegue en producción (detrás de un reverse-proxy / túnel)

Para exponer la app a Internet, publica **solo el frontend** (nginx con el build, servicio
`gen-suite-frontend`) detrás de tu reverse-proxy o túnel (Cloudflare Tunnel, Traefik, Caddy, nginx…) en
tu dominio (p. ej. `https://TU-DOMINIO`). API, worker, Postgres, Redis y MinIO se quedan en la red interna.

## Endurecimiento (configúralo en `.env`)
- Secretos fuertes: `JWT_SECRET`, `SUITE_MASTER_KEY` (AES-256, `openssl rand -base64 32`), contraseñas de
  Postgres/app/MinIO y `REDIS_PASSWORD`. `ENVIRONMENT=production`, `DEBUG=false`,
  `ALLOW_FIRST_USER_ADMIN=false`, `CORS_ORIGINS=["https://TU-DOMINIO"]`.
- **Validación de arranque**: con `ENVIRONMENT=production` la API **se niega a arrancar** si algún
  secreto es un placeholder/valor de ejemplo (ver `settings.validate_runtime_secrets`).
- **Redis con contraseña** (`--requirepass`), y `REDIS_URL` con credenciales en backend/worker.
- El proxy/túnel apunta a `gen-suite-frontend:80` (build estático), **no** al dev-server de Vite.

## Pasos de despliegue
1. `cp config/.env.example config/.env` y rellena los secretos; `docker compose --env-file config/.env up -d --build`.
2. **Primer arranque — bootstrap del admin**: pon temporalmente `ALLOW_FIRST_USER_ADMIN=true` en `config/.env`,
   `docker compose --env-file config/.env up -d --build`, **regístrate** (ese primer usuario será server-admin),
   y vuelve a poner `ALLOW_FIRST_USER_ADMIN=false` + `docker compose --env-file config/.env up -d` para
   recargar. Después nadie más se autopromociona.
3. Apunta tu proxy/túnel a `gen-suite-frontend:80` y verifica: `https://TU-DOMINIO` carga el login; la
   API responde 401 sin token.

> Para el despliegue privado del autor (túnel Cloudflare/Dockflare, redes externas, IPs de LAN) usa el
> overlay `compose.micapum.yaml` (ignorado por git): `docker compose -f compose.micapum.yaml up -d --build`.

## Almacenamiento: qué va a S3 vs Postgres
| Dato | Dónde | Por qué |
|---|---|---|
| PDFs originales, imágenes de página (WebP/JPG), fotos de personas, descargas FamilySearch | **S3 / MinIO** | Blobs grandes, solo se leen por clave. Es para lo que sirve el almacenamiento de objetos. |
| Texto OCR, actas, menciones, **embeddings** (pgvector) | **Postgres** | Son datos **consultables** (full-text, extracción, búsqueda vectorial). En S3 no se pueden consultar. |
| La BBDD viva | **Disco** (`./data/postgres`) | Una BD no puede correr sobre S3. Se respalda a S3 (abajo). |

### MinIO bundled (por defecto)
La biblioteca se guarda en el MinIO del stack, con los datos en `./data/minio` (host-mapeado).
`COMPOSE_PROFILES=bundled-storage` en `config/.env` lo activa.

### S3 externo (AWS / Backblaze / Wasabi)
1. Pre-crea los **dos buckets** (privado y público) en tu proveedor.
2. En `config/.env`: `COMPOSE_PROFILES=` (vacío, para no arrancar el MinIO local) y
   `MINIO_ENDPOINT` (p. ej. `s3.eu-west-1.amazonaws.com`), `MINIO_SECURE=true`, `MINIO_REGION`,
   `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`, `MINIO_BUCKET_PRIVATE`/`MINIO_BUCKET_PUBLIC`. (Backblaze/Wasabi:
   añade `MINIO_ADDRESSING_STYLE=path` si hay errores de addressing.)
3. `docker compose --env-file config/.env up -d --build` → no arranca MinIO; todo va a tu S3.
   El acceso a los blobs sigue siendo **a través de la API** (auth + RLS), nunca por URL pública, así que
   ambos buckets pueden ser privados a nivel de S3.

### Backups de la BBDD a S3
`BACKUP_TO_S3=true` (en `config/.env`) activa un `pg_dump` diario (hora `BACKUP_HOUR` UTC) que sube un dump
en formato custom a `{MINIO_BUCKET_PRIVATE}/_backups/gensuite-FECHA.dump`, conservando los `BACKUP_RETENTION`
más recientes. **Restaurar**: descarga el `.dump` y
`pg_restore --clean --no-owner -h <host> -U <POSTGRES_USER> -d <POSTGRES_DB> archivo.dump`.

## Avisos importantes
- **Volumen de Postgres**: las contraseñas solo se aplican al **inicializar** el volumen `pgdata`. Si ya
  habías desplegado con contraseñas antiguas, el volumen conserva las viejas y el backend no conectará.
  Para una BD ya existente, cambia la contraseña dentro de Postgres (o parte de un volumen limpio).
- **Rotación de `SUITE_MASTER_KEY`**: si más adelante la rotas, las credenciales de proveedor guardadas
  quedan ilegibles salvo que las re-cifres (script de migración) o las vuelvas a introducir.
- **MinIO**: la consola (`127.0.0.1:9001` en `compose.yaml`) es solo de administración local. **No la publiques** por el proxy/túnel.
- **FamilySearch** (`FS_CONNECTOR_ENABLED`): las descargas validan que la URL sea `https://…familysearch.org`
  (anti-SSRF). Las cookies del operador van cifradas y solo las leen server-admins.

## Arquitectura (ARM vs x86) — la IA NO depende de la arquitectura
Toda la IA (transcripción, extracción, **embeddings**) son **llamadas HTTP a APIs cloud** (OpenRouter / Google
Gemini / Jina / Ollama Cloud) vía cliente OpenAI-compatible — no hay inferencia local ni `torch`/`onnx`. Los
embeddings se generan con **Jina** (`jina-embeddings-v3`, 1024 dim) por HTTPS, así que funcionan **igual en Mac
ARM, x86 o cualquier sitio**. Las únicas dependencias con binarios (`pymupdf`, `pillow`) traen ruedas para
**amd64 y arm64**, así que `pip install` coge la correcta según el destino. Las imágenes base
(`pgvector/pgvector:pg16`, `redis`, `minio`, `python:3.12-slim`, `nginx`) son **multi-arch**.

**Único matiz real:** las imágenes se construyen para la arquitectura donde haces `docker build`.
- **Recomendado:** construir **en el servidor de destino** (x86): `git pull && docker compose up -d --build` → x86 automático.
- Si construyes en tu **Mac (ARM)** para desplegar en x86: `docker buildx build --platform linux/amd64 …`
  (o añade `platform: linux/amd64` a los servicios de `compose.yaml`).
- El `.venv-local` (binarios ARM del dev) **no entra** en la imagen (`.dockerignore`); el contenedor hace su
  propio `pip install` para su arquitectura.

## Desarrollo local (no afectado por lo anterior)
El stack nativo de desarrollo usa `backend/.env` (sin `ENVIRONMENT` → `development`), así que la
validación estricta no aplica. Servicios: API `uvicorn app.main:app`, worker `backend/run_worker.sh`
(supervisado), y Vite `npx vite --host`. Datastores en contenedores `gs-db`/`gs-redis`/`gs-minio`.
