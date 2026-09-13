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
- Activa `INVITATION_ONLY=true`, `API_DOCS_ENABLED=false` y deja `GRAMPS_SYNC_ENABLED=false` hasta que
  exista una validación bidireccional completa. Configura SMTP con SPF, DKIM y DMARC: las invitaciones y
  los restablecimientos de contraseña son enlaces de un solo uso.
- El puerto del frontend queda ligado a `127.0.0.1`; publica únicamente 80/443 desde Caddy/nginx, con TLS,
  firewall y SSH limitado a IPs de administración. `/healthz` comprueba proceso y `/readyz` comprueba
  Postgres, Redis y S3: el monitor externo debe consultar este último.

## Instalación principal: Docker Compose

Solo necesitas Docker con Compose 2.20+ y acceso al daemon (`docker info`). No hace falta instalar
Python ni Node en el servidor, ejecutar `install.sh` ni crear `config/installer.json`.

### 1. Preparar la configuración una sola vez

Si ya existe `config/.env`, consérvalo. En una instalación nueva:

```bash
cp -n config/.env.example config/.env
chmod 600 config/.env
```

Edita `config/.env` antes del primer arranque:

- Pon valores aleatorios distintos en `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `REDIS_PASSWORD`,
  `MINIO_SECRET_KEY` y `JWT_SECRET`. Puedes generar cada uno con `openssl rand -hex 32`.
- Genera `SUITE_MASTER_KEY` con `openssl rand -base64 32`.
- Ajusta `FRONTEND_PUBLIC_URL` y `CORS_ORIGINS` a tu dominio HTTPS y configura SMTP (STARTTLS).
- Mantén `ENVIRONMENT=production`, `DEBUG=false`, `ALLOW_FIRST_USER_ADMIN=false` e `INVITATION_ONLY=true`.

No cambies los secretos al reiniciar. No publiques el archivo ni ejecutes `source config/.env`.
Compose aplica las variables exportadas del shell por encima de `--env-file`: evita exportar valores
distintos para las claves de la suite. Si existen datos pero falta su `.env`, restaura la configuración
original antes de arrancar; generar otras contraseñas no recupera el acceso.

### 2. Arrancar el stack

```bash
docker compose --env-file config/.env up -d --build --wait --wait-timeout 180
```

`compose.yaml` contiene la construcción de la imagen compartida por API, worker y migraciones:
no necesita que un script la construya previamente. Compose espera a Postgres y Redis, y a MinIO
cuando está activo. La API y el worker requieren que las migraciones terminen correctamente.
`--wait` también comprueba la salud del backend y del frontend; amplía `--wait-timeout` si el host es lento.
En una instalación nueva, una migración fallida bloquea el arranque de la aplicación.

### PDF/A opcional

Para convertir y validar derivados PDF/A-2b, activa el perfil `pdfa`:

```bash
docker compose --env-file config/.env --profile pdfa up -d --build --wait --wait-timeout 180
```

Mantén `pdfa` en `COMPOSE_PROFILES` si quieres conservarlo en futuros arranques.
Es un worker independiente, sin puerto público y con temporales/memoria limitados;
no añade herramientas al host ni consume IA al exportar. Versiones, límites, prueba
sintética y uso con imágenes publicadas: [PDF/A opcional](docs/PDFA.md).

### 3. Crear la administración inicial

```bash
docker compose --env-file config/.env run --rm --no-deps gen-suite-backend python -m app.bootstrap_admin
```

El comando usa Python **dentro del contenedor**, pide correo y contraseña oculta y mantiene cerrado
el bootstrap público. Solo crea una cuenta cuando la base está vacía; conserva la administración
existente y nunca promociona otras cuentas. No incluyas la contraseña en argumentos ni en el `.env`.

### 4. Conectar HTTPS y comprobar el acceso

Conecta tu proxy TLS del host a `127.0.0.1:8080` (o al puerto `FRONTEND_PORT`). DNS, TLS, firewall y
SMTP se administran aparte. En el dominio público verifica el login y `/readyz` (JSON con
`status=ready`): la comprobación interna de Docker no certifica el acceso desde Internet.

Consulta estado y registros con Compose; los logs pueden contener datos privados, no los publiques:

```bash
docker compose --env-file config/.env ps --all
docker compose --env-file config/.env logs --tail 100 gen-suite-migrate gen-suite-backend gen-suite-frontend
```

### Instalar desde imágenes publicadas (sin build)

Copia `compose.release.yaml` y `config/.env.example` al servidor, prepara `config/.env` como arriba y
fija `GEN_SUITE_VERSION` a una versión **publicada** que incluya `app.bootstrap_admin`. No se presupone
que esta revisión ya esté publicada en GHCR. Después:

```bash
docker compose -f compose.release.yaml --env-file config/.env pull
docker compose -f compose.release.yaml --env-file config/.env up -d --wait --wait-timeout 180
docker compose -f compose.release.yaml --env-file config/.env run --rm --no-deps gen-suite-backend python -m app.bootstrap_admin
```

Usa siempre el mismo `-f compose.release.yaml` al consultar estado, logs o actualizar este despliegue.

### Actualizar una instalación

Realiza y verifica un backup antes de actualizar. Para instalaciones desde código, incorpora la
revisión elegida y repite `docker compose --env-file config/.env up -d --build --wait --wait-timeout 180`.
Para releases, cambia solo `GEN_SUITE_VERSION` en `config/.env` a la versión publicada elegida y repite
`pull` y `up` con `-f compose.release.yaml`. Compose no consulta `config/installer.json`.
No hace falta recrear la administración, regenerar configuración ni borrar volúmenes.
Backend, worker y migraciones deben utilizar la misma imagen. La reversión de una migración requiere
el procedimiento de recuperación de datos; cambiar solo la imagen no revierte el esquema.

### Asistente opcional

`install.sh` se conserva para quien prefiera preguntas guiadas, pero no es un requisito de instalación:

- Con Python 3.10+ en el host, `./install.sh --domain arbol.example.org --configure-only` genera una
  configuración nueva y un ejemplo de Caddy; después puedes seguir exclusivamente con Compose.
- `./install.sh` ejecuta Compose por fases, genera secretos solo si falta `.env` y conserva las cuentas.
- Solo el asistente consulta `config/installer.json`. Si vuelves a él tras cambiar una release mediante
  Compose, sincroniza su campo `release` con `GEN_SUITE_VERSION`; para builds locales usa `release: null`.
- Si ya usas un overlay privado o nombres distintos, conserva tu procedimiento Compose.

Pruebas de configuración Compose y del asistente: `python3 -m unittest discover -s scripts/tests -v`;
no usan el `.env` ni los datos del despliegue. La CI prueba bootstrap contra una base desechable.

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

El perfil bundled sirve para evaluación y uso privado controlado, pero la edición
comunitaria fijada tiene un [aviso de seguridad pendiente](docs/ESTADO_VALIDACION_REAL.md#minio-multipart-corregido-bloqueo-de-seguridad-pendiente).
Para una exposición pública usa por ahora un S3 mantenido y acreditado; no presentes
el perfil bundled como almacenamiento aprobado de producción.

### S3 externo (AWS / Backblaze / Wasabi)
1. Pre-crea los **dos buckets** (privado y público) en tu proveedor.
2. En `config/.env`: `COMPOSE_PROFILES=` (vacío, para no arrancar el MinIO local) y
   `MINIO_ENDPOINT` (p. ej. `s3.eu-west-1.amazonaws.com`), `MINIO_SECURE=true`, `MINIO_REGION`,
   `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`, `MINIO_BUCKET_PRIVATE`/`MINIO_BUCKET_PUBLIC`. (Backblaze/Wasabi:
   añade `MINIO_ADDRESSING_STYLE=path` si hay errores de addressing.)
3. `docker compose --env-file config/.env up -d --build` → no arranca MinIO; todo va a tu S3.
   El acceso a los blobs sigue siendo **a través de la API** (auth + RLS), nunca por URL pública, así que
   ambos buckets pueden ser privados a nivel de S3.

### Backups y restauración

`BACKUP_TO_S3=true` activa un dump diario **solo de PostgreSQL** (hora `BACKUP_HOUR` UTC)
en `BACKUP_S3_BUCKET`. No protege los JPG/PDF de S3 por sí solo. Ahora transfiere desde
un archivo temporal privado y declara expresamente su alcance en un descriptor SHA-256.

La herramienta [Recuperación completa](docs/RECUPERACION_COMPLETA.md) permite copiar
PostgreSQL **más ambos buckets**, verificar, subir/descargar del almacén de backup y
restaurar únicamente en otro clúster y buckets vacíos, con cuarentena. Se ejecuta mediante
el overlay opcional `compose.recovery.yaml`; requiere detener todos los escritores y
custodiar por separado configuración y clave maestra. No borra copias ni utiliza `--clean`.

Configura y comprueba cifrado, versionado y retención/Object Lock del destino con una
cuenta separada: el código no acredita esas políticas. Exige un ensayo real de restauración
antes de la release; la verificación de checksums o `/readyz` por sí solos no bastan.

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
- **Recomendado:** construir **en el servidor de destino** (x86): `docker compose --env-file config/.env up -d --build --wait` → x86 automático.
- Si construyes en tu **Mac (ARM)** para desplegar en x86: `docker buildx build --platform linux/amd64 …`
  (o añade `platform: linux/amd64` a los servicios de `compose.yaml`).
- El `.venv-local` (binarios ARM del dev) **no entra** en la imagen (`.dockerignore`); el contenedor hace su
  propio `pip install` para su arquitectura.

## Desarrollo local (no afectado por lo anterior)
El stack nativo de desarrollo usa `backend/.env` (sin `ENVIRONMENT` → `development`), así que la
validación estricta no aplica. Servicios: API `uvicorn app.main:app`, worker `backend/run_worker.sh`
(bucle supervisado **solo para dev nativo**; en Docker el worker es el servicio compose con
`arq app.tasks.worker.WorkerSettings`), y Vite `npx vite --host`. Datastores en contenedores
`gs-db`/`gs-redis`/`gs-minio`.
