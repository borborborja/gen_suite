# Copia y recuperación de una instalación completa

Implementación para **PostgreSQL 16 y esquemas `0044` a `0053`**. El soporte de `0052/0053`
incluye resultados privados de exportación, originales de importación del árbol y
vínculos múltiples entre fuentes y documentos. Es una herramienta de operador,
no una ruta HTTP ni una exportación accesible
a miembros de un espacio. Incluye todos los espacios y datos de identidad: **la copia
es confidencial**. Pasa un ensayo completo con PostgreSQL/S3 reales y datos sintéticos;
siguen pendientes escala, más fallos y operación con colecciones representativas.

El ensayo completo descrito abajo corresponde a `0053` y se ha repetido entre dos
clústeres PostgreSQL 16 y dos servidores S3 sintéticos independientes.

## Tres productos distintos

- GEDCOM: [intercambio genealógico con revisión de pérdidas](INTERCAMBIO_GEDCOM.md),
  sin recuperación completa del espacio. [GEDZIP](EXPORTACION_ARBOL.md) añade originales
  propios, pero tampoco sustituye el backup.
- BagIt por documento: preservación de un libro, no de toda la instalación.
- `python -m app.recovery`: recuperación conjunta de PostgreSQL y los objetos actuales
  de los dos buckets. No sustituye la futura exportación/importación portable por espacio.

El cron `BACKUP_TO_S3` sigue siendo **solo PostgreSQL**. Se corrigió para escribir el dump
a un archivo privado temporal y subirlo mediante transferencia de archivo; no carga
todo el dump en RAM. Sus objetos se identifican como `_backups/database-only/` y tienen
un descriptor SHA-256 que declara `objects_included=false`. Un error ahora hace fallar
la tarea; no se imprime la excepción del proveedor, que podría contener información privada.

## Qué conserva la copia conjunta

`create` genera un directorio nuevo, sin sobrescribir uno anterior:

| Componente | Contenido y comprobación |
| --- | --- |
| `database.dump` | Dump custom: árbol, fuentes, citas, actas, revisiones, investigación, auditoría, índices, identidad y resto de tablas. SHA-256 y tamaño. |
| `objects.jsonl` | Inventario de objetos privados/públicos con claves originales, bytes, SHA-256, ETag, tipo de contenido, metadatos y versión observada. |
| `objects/private/…`, `objects/public/…` | Bytes completos de cada objeto actual; los nombres locales derivan del hash de su clave, nunca de rutas facilitadas por el origen. |
| `references.jsonl` | Referencias de PostgreSQL: páginas no purgadas, PDF original retenido, fotos, exportaciones, respuestas de IA, originales y fragmentos de importación. Comprueba presencia y checksum/tamaño cuando constan. |
| `manifest.json` | Marcador final v2, identidad del clúster, esquema, recuentos, columnas, restricciones, índices, funciones, triggers, extensiones, secuencias, ACL/RLS, huella SHA-256 enmarcada de todas las filas de cada tabla y exclusiones. Solo se escribe tras verificar el conjunto. |

El inventario incluye objetos actuales aunque no estén referenciados, por ejemplo
fragmentos persistidos antes de una caída. La verificación usa un índice SQLite temporal
y transferencias de 1 MiB; no crea un diccionario Python con todo el corpus. Antes de
copiar cada objeto comprueba espacio para sus bytes y un margen de 128 MiB.
El dump también necesita espacio: utiliza **un volumen dedicado y dimensionado**, no
el disco que mantiene PostgreSQL. La [cuota de contenido de aplicación](CUOTA_ALMACENAMIENTO.md)
no cubre ese volumen ni el backup externo. No hay reanudación de una copia parcial.

No incluye configuración, claves en claro, contraseñas de roles, otras bases de datos,
tablespaces externos, Redis, versiones S3 anteriores, originales ya eliminados ni cargas
multipart todavía sin finalizar. Las claves cifradas de aplicación sí están en el dump;
necesitan la `SUITE_MASTER_KEY` original, custodiada por separado. Se compara su huella
antes de restaurar; no se guarda la clave en el manifiesto. Los checksums no prueban
la autenticidad de una copia manipulada por un atacante.

## Consistencia: ventana de mantenimiento obligatoria

Detén **todos** los escritores durante `create`: API, workers, importadores, sincronizadores
y clientes externos con acceso a PostgreSQL o S3. `--confirm-quiesced` es una declaración
del operador, **no un bloqueo universal de escrituras**. La herramienta comprueba otras
conexiones a la BD antes y después y compara dos inventarios S3, pero no puede impedir
que un cliente externo se conecte entre esas observaciones.

El dump y la captura de metadatos/referencias comparten un snapshot PostgreSQL exportado.
PostgreSQL permite a `pg_dump` utilizar ese snapshot; no extiende esa garantía a S3.
[Contrato de pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html).

Los objetos se leen con `IfMatch` y su ETag observado, además del SHA-256 calculado de los
bytes. Un cambio de ETag hace fallar la lectura condicional; **el ETag no se trata como
checksum criptográfico del archivo**. [Contrato de GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html).

Un proveedor de IA puede seguir ejecutando una petición enviada antes de detener el
worker. Por eso una restauración conserva reservas y convierte llamadas `inflight` en
`unknown`, sin declarar que son gratuitas ni repetirlas automáticamente.

## Uso con Compose

El overlay opcional `compose.recovery.yaml` usa la imagen backend ya construida. No añade
servicios al arranque habitual, no publica puertos, no monta el socket Docker ni `./data`.
El único bind de copias es de lectura por defecto y no crea su directorio en el host.

Prepara un directorio de copias con espacio y permisos para tu UID/GID; el ejemplo asume
`/srv/gen-suite-recovery`. No uses la carpeta de datos viva. Desde la instalación de origen:

```bash
export GEN_SUITE_RECOVERY_DIR=/srv/gen-suite-recovery
export GEN_SUITE_RECOVERY_UID="$(id -u)"
export GEN_SUITE_RECOVERY_GID="$(id -g)"
```

Para una instalación release, usa `compose.release.yaml` en lugar de `compose.yaml` y
define `GEN_SUITE_RECOVERY_IMAGE` con **la imagen exacta que contiene este código**, no
una etiqueta de una release anterior. No ejecutes una imagen nueva contra otro esquema.

En la ventana de mantenimiento aprobada, detén los servicios que escriben; detén también
los importadores o clientes que no estén en estos tres servicios:

```bash
docker compose --env-file config/.env stop gen-suite-frontend gen-suite-backend gen-suite-worker

GEN_SUITE_RECOVERY_READ_ONLY=false docker compose --env-file config/.env \
  -f compose.yaml -f compose.recovery.yaml run --rm --no-deps gen-suite-recovery \
  create /recovery/copia-001 --confirm-quiesced --confirm-key-escrow
```

No hay reinicio automático. Una vez terminada la copia, el operador decide cuándo
arrancar los mismos servicios, con su configuración y versión originales.

Verificación independiente, con el bind de solo lectura:

```bash
docker compose --env-file config/.env -f compose.yaml -f compose.recovery.yaml \
  run --rm --no-deps gen-suite-recovery verify /recovery/copia-001
```

`verify` no necesita servicios ni lee la configuración desde Python. Compose sigue
cargando su entorno al crear el contenedor. Con Python y las dependencias ya disponibles,
`python -m app.recovery verify /ruta/copia-001` es completamente local y sin red.

## Copia externa

Configura `BACKUP_S3_*` para una cuenta y bucket distintos a los de la aplicación, con
TLS, cifrado y retención/versionado/Object Lock según tu proveedor. El código **no configura
ni acredita esas políticas**. La cuenta necesita listar, escribir y leer para comprobar los
bytes, pero no borrar. `BACKUP_TO_S3` no gobierna estas órdenes manuales.

```bash
docker compose --env-file config/.env -f compose.yaml -f compose.recovery.yaml \
  run --rm --no-deps gen-suite-recovery upload /recovery/copia-001
```

Se crea un prefijo nuevo `_backups/full/<bundle_id>/`, se vuelve a leer cada archivo subido
y se publica el manifiesto al final. Guarda el `bundle_id` del resultado. No hay borrado
ni sustitución de copias anteriores. La verificación implica tráfico de subida y bajada;
cuenta ese tráfico y las solicitudes en el coste de almacenamiento.

Para recuperar una copia remota, sustituye `UUID_DEL_BUNDLE` por ese identificador:

```bash
GEN_SUITE_RECOVERY_READ_ONLY=false docker compose --env-file config/.env \
  -f compose.yaml -f compose.recovery.yaml run --rm --no-deps gen-suite-recovery \
  download UUID_DEL_BUNDLE /recovery/descarga-001
```

Una transferencia fallida conserva sus archivos parciales, pero no se da por completada.
Reintenta en un directorio/prefijo nuevo; la limpieza de parciales no es automática.

## Restauración: solo en un destino vacío y aislado

Prepara **otra VM/host o daemon Docker independiente**, sin tráfico público ni salida
a proveedores de IA/conectores. No basta cambiar `-p` de Compose: los ficheros de la suite
usan nombres de contenedor explícitos y no deben compartir los datos del origen.

No ejecutes `install.sh` ni migraciones en el destino: inicializarían tablas que harían
dejar de estar vacía la BD. Prepara únicamente PostgreSQL 16 con las mismas extensiones
disponibles y S3/MinIO. La configuración aislada debe cumplir:

- BD nueva `gensuite_restore_*` en un clúster cuyo `system_identifier` difiera del origen.
  No debe haber otras BD de usuario en ese clúster aparte de `postgres` y la del ensayo.
- Mismos nombres de roles propietario/aplicación para conservar ACL; contraseñas nuevas.
  El rol de aplicación no puede ser privilegiado ni heredar otros roles.
- Dos buckets **precreados y vacíos** `gensuite-restore-*`, diferentes de los originales,
  sin versiones antiguas, marcas de borrado ni cargas multipart pendientes. La comprobación
  requiere permisos para listar versiones/multipart; un proveedor que no los admita falla cerrado.
- La clave maestra original desde su custodia externa; configuración JWT, correo, conectores
  y publicación revisada para el ensayo, sin reutilizar accesos externos por accidente.
- Copia montada de solo lectura. API y workers permanecen detenidos.

Desde **ese destino**, con su configuración aislada:

```bash
docker compose --env-file config/.env -f compose.yaml -f compose.recovery.yaml \
  run --rm --no-deps gen-suite-recovery restore /recovery/descarga-001 \
  --confirm-empty-isolated --trust-backup --confirm-key-escrow
```

El dump ejecuta SQL. Acepta únicamente copias propias de confianza: ni el prefijo de BD
ni las comprobaciones de checksum hacen seguro un dump malicioso. Se usa
`pg_restore --no-owner --exit-on-error --single-transaction`, nunca `--clean` ni `--create`.
[Advertencia y opciones de pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html).

Se verifican metadatos/esquema, triggers, recuentos, secuencias, privilegios, políticas y
la huella del contenido completo de cada tabla; después se
restauran los objetos y se releen todos sus bytes y metadatos. Se comprueban lecturas RLS
con hasta dos espacios existentes y otro inexistente, incluyendo lectura positiva de
evidencia propia para que «todo devuelve cero» no cuente como aislamiento correcto. Esto no sustituye las pruebas
completas HTTP y de escritura concurrente de la suite.

Los indicadores `attidentity`/`attgenerated` del catálogo se convierten explícitamente
de `"char"` interno a texto para serializar el manifiesto. La comparación reconoce
únicamente la redistribución equivalente de casts `varchar[] → text[]` sobre arrays
de literales sin límite de longitud: PostgreSQL puede producirla al reconstruir una
restricción o un índice. No se ignoran restricciones, índices ni permisos; se conservan
nombres, valores, orden, predicados y todas las demás propiedades. Expresiones no
reconocidas requieren igualdad exacta. El manifiesto original no se modifica.
[Reconstrucción del catálogo](https://www.postgresql.org/docs/16/functions-info.html),
[explicación del cambio de casts por PostgreSQL](https://www.postgresql.org/message-id/1fa24f41862f214b44bcf94556db51c9946d28bd.camel@cybertec.at).

Antes de devolver éxito, la herramienta adapta los buckets de documentos y deja en
cuarentena trabajos/investigaciones activos, proveedores y presupuestos. Conserva las
reservas de IA. **El rol de aplicación queda `NOLOGIN`**, incluso al finalizar; no se
arrancan servicios. El informe describe esas modificaciones y mantiene `production_ready=false`.
El dump original no se modifica. PostgreSQL y S3 no forman una transacción conjunta:
si falla S3, queda un destino parcial aislado que no se habilita ni limpia automáticamente.

En copias `0046` a `0053`, una limpieza de staging pendiente empieza otra época de inventario al
restaurar, ya que los objetos del destino tienen otras versiones S3. Conserva los recibos
anteriores y exige reintento explícito después de revisar el destino; no se activa sola.

En copias `0047` a `0053`, todos los inventarios de almacenamiento del origen se conservan
invalidados. Las versiones históricas y cargas multipart del origen no se restauran
como tales, por lo que no se reutilizan sus cursores ni sus recuentos como medida del
destino. Crear un [inventario nuevo](INVENTARIO_ALMACENAMIENTO.md) tras revisar el destino.

En copias `0048` a `0053`, los recibos S3 pendientes pasan a `quarantined` y la cuota queda
`uninitialized`, sin ubicación ni línea base activa. No se trasladan reservas de partes
no restauradas como si estuvieran en el destino; se conserva el historial y se exige
un [recuento protegido nuevo](CUOTA_ALMACENAMIENTO.md) antes de admitir escrituras sobre
los objetos restaurados. La conciliación de posibles costes de IA sigue siendo necesaria.

En copias `0049` a `0053`, se conserva además la auditoría de reservas canceladas. El
recuperador mantiene el esquema original; una copia `0049` no adquiere por restaurarse
la revocación de privilegios de `0050`. No habilitar una versión de aplicación que exija
un esquema distinto sin validar y aplicar su actualización en el destino aislado.

El operador debe guardar el informe, revisar resultados, establecer la contraseña del
rol de aplicación en el entorno aislado y habilitar su login solo para las pruebas que
autorice. Después debe verificar acceso, imágenes, fuentes, árboles, revisiones, búsquedas,
aislamiento y conciliación de IA antes de considerar un cambio de servicio.

## Verificación de esta entrega y pendientes

- Pasa **un ensayo completo real con datos sintéticos**, desde un origen nuevo hasta
  otro clúster PostgreSQL 16 y otro servidor S3. No es solo `pg_dump` ni una comparación
  de respuestas simuladas. El destino vuelve a crearse desde cero tras corregir los
  defectos encontrados; los intentos parciales anteriores se conservan aparte.
- El fixture crea mediante HTTP dos espacios, seis personas, dos árboles familiares,
  fuentes/citaciones con folio, dos originales PNG, sus revisiones textuales y PDFs
  de consulta. Incluye vectores históricos, credenciales cifradas ficticias y estados
  pendientes de investigación, IA, inventario y reservas S3. No ejecuta IA.
- **10 objetos, 6 referencias, 234 comprobaciones RLS** en la ejecución local de `0053`.
  Se verifican bytes originales, mapeo de buckets privado/público, huellas de todas las
  tablas y hashes adicionales de 14 tablas de evidencia, descifrado con
  la clave custodiada y rol de aplicación `NOLOGIN` sin superusuario ni bypass RLS.
- Los trabajos e investigaciones quedan pausados; las llamadas IA en curso, inciertas;
  los proveedores, desactivados; los inventarios, invalidados; dos reservas nunca
  enviadas, en cuarentena; las cuotas, sin línea base activa. El origen no se modifica.
- Se rechazan una copia con otra conexión activa, una clave maestra incorrecta y
  una segunda restauración sobre el destino poblado. Se mantienen las pruebas de
  corrupción y otros fallos parciales, sin confundir contratos simulados con este ensayo.
- La nueva regresión PostgreSQL exige que todo el catálogo real sea JSON serializable.
  Cinco tests y 22 subtests adicionales comprueban la comparación de esquema,
  incluidos cambios de valores, casts, unicidad, permisos y políticas que deben fallar.
- La suite unitaria actual pasa **265 pruebas**; la integración dentro de la imagen
  runtime pasa **172 pruebas** y omite una que pertenece al perfil PDF/A especializado.
- `.github/workflows/recovery.yml` prepara dos PostgreSQL, Redis y dos S3 independientes
  y ejecuta el ensayo usando los clientes PG16 de la imagen. Conserva el bundle
  sintético o parcial tres días. La misma receta se ha ejecutado localmente contra la
  imagen runtime; la ejecución publicada de GitHub continúa siendo la evidencia de release.
- No se han copiado datos personales, contratado almacenamiento, subido objetos a
  servicios externos ni cambiado la instalación personal. MinIO comunitario sigue
  siendo un backend de prueba con [bloqueos pendientes](ESTADO_VALIDACION_REAL.md).

### Repetir el ensayo sintético

`backend/checks/smoke_recovery.py` no es un recuperador de datos personales. No crea
servicios, no migra, no trunca ni elimina bases de datos; requiere un origen migrado
vacío `gensuite_test_*`, un destino vacío `gensuite_restore_*` en otro clúster y
cuatro buckets vacíos precreados en dos servidores S3. Rechaza SMTP y backup automático.
Si un intento deja datos, prepara **otros** destinos de prueba: no fuerza un reinicio.

El workflow es la receta completa con configuración sintética explícita. Fuera de CI,
proporciona las variables habituales **solo de los servicios desechables**, más
`GEN_SUITE_RUN_RECOVERY_INTEGRATION=yes` y las variantes `RECOVERY_TEST_TARGET_` de:

```text
POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
APP_DB_USER APP_DB_PASSWORD MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY
MINIO_BUCKET_PRIVATE MINIO_BUCKET_PUBLIC MINIO_SECURE MINIO_REGION
MINIO_ADDRESSING_STYLE SUITE_MASTER_KEY
```

Con un padre preparado y el nombre de bundle todavía inexistente:

```bash
python checks/smoke_recovery.py /recovery/bundle-sintetico
```

El ensayo conserva archivos y datos para inspección; no imprime contraseñas,
credenciales ni respuestas HTTP completas cuando falla. No certifica libros reales,
capacidad de producción, compatibilidad general de S3 ni seguridad del backend antiguo.

Pendientes: más inyección de fallos reales, escala/espacio/tiempos de recuperación,
retención de parciales, custodia probada de claves y políticas del backup externo,
automatización segura de ventanas de mantenimiento y réplica/PITR si se necesitan.
La exportación/restauración nativa **portable por espacio** sigue pendiente. Existe
[importación revisada GEDCOM 7/GEDZIP](IMPORTACION_ARBOL.md), sin restauración completa;
PDF/A ya tiene un recorrido sintético real validado, pero no prueba
Compose ni libros representativos. Véase el [plan completo](IMPLEMENTACION_INVESTIGACION.md).
