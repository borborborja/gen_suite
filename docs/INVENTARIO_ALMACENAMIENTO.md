# Inventario de almacenamiento por espacio

**Bloqueo detectado con servicios reales:** el MinIO comunitario incluido devuelve
un listado multipart vacío para el prefijo del espacio aunque existan partes. El
código actual puede subcontar y marcar cobertura completa indebidamente; las dos
pruebas de regresión fallan y bloquean la release. No usar sus resultados como línea
base de una cuota. Detalles y sustitución pendiente de autorización:
[Estado de validación real](ESTADO_VALIDACION_REAL.md).

Entrega `0047`: inventario reanudable y **observacional**, visible en Ajustes. Lee
metadatos de S3/MinIO y guarda el resultado en PostgreSQL. No escribe, descarga,
modifica ni elimina objetos S3; no ejecuta IA. No es una cuota. La entrega posterior
`0048` añade [admisión de escrituras y recuento protegido](CUOTA_ALMACENAMIENTO.md),
con controles separados y sin activar retrospectivamente estos informes observacionales.

## Qué resuelve

La reserva de una importación no representa todo el almacenamiento de una colección.
Este recorrido incluye objetos antiguos sin referencia en PostgreSQL, originales,
páginas, PDFs/ZIP de exportación, manifiestos, imágenes de personas, respuestas de IA
y temporales. Cuenta por separado las copias presentes en el bucket privado y público
del mismo espacio; no cuenta documentos públicos propiedad de otros espacios.

Incluye versiones vigentes, versiones no vigentes y marcadores de borrado. Listar
solo los objetos actuales omitiría ese historial. El recorrido conserva los dos
marcadores de paginación de versiones y el identificador literal `null` cuando lo
devuelve el almacenamiento. [Contrato de ListObjectVersions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectVersions.html).

También enumera las cargas multipart sin terminar e inspecciona sus partes ya subidas.
Estas partes pueden generar costes de almacenamiento aunque todavía no exista un
objeto terminado. Cada carga usa su `UploadId`; sus partes tienen otro cursor, por
número de parte. No se completa ni aborta ninguna carga durante el inventario.
[Listado de cargas multipart](https://docs.aws.amazon.com/AmazonS3/latest/userguide/list-mpu.html),
[ListMultipartUploads](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListMultipartUploads.html),
[ListParts](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListParts.html).

## Uso en la aplicación

1. En **Ajustes → Inventario de almacenamiento**, documentar el motivo y pulsar
   **Inventariar sin modificar archivos**. Solo administración puede iniciarlo.
2. Abrir el inventario en su historial. Se muestran fase, estado, intervalo temporal,
   responsable y bytes de contenido por almacén y categoría. La clasificación se basa
   en la ruta del objeto; no acredita que el contenido sea un original auténtico.
3. Si falla una petición de listado, corregir conexión/permisos y usar **Reanudar el
   mismo inventario**. Conserva entradas y cursores ya confirmados, el mismo trabajo
   y su envío duradero. No repite desde cero ni duplica el recuento confirmado.
4. Si el proveedor repite identidades o hay paginación incompatible, revisar la
   incidencia e iniciar otro inventario. Los resultados anteriores se conservan.
5. **Cancelar solo el inventario** detiene las lecturas posteriores de forma
   cooperativa. La petición de lectura que esté en curso puede terminar. No se
   interrumpen importaciones ni se borran partes, versiones o resultados parciales.

Los miembros pueden consultar los totales de su espacio; administración controla
creación, cancelación y reanudación. La API no entrega nombres de buckets, claves de
objetos, cursores ni credenciales. S3 permanece interno y los binarios siguen pasando
por el backend. Las peticiones de listado pueden tener coste en un proveedor externo.

La instalación sigue siendo Docker Compose, sin dependencias ni servicios adicionales.
La migración añade tablas vacías y políticas RLS: no hace backfill ni cambia documentos
existentes. No se lanza automáticamente ningún inventario al actualizar o al arrancar.

## Contrato y límites de medición

`observed_payload_bytes` suma los tamaños de versiones y partes observadas. No suma
los marcadores como si fueran contenido: muestra su número y advierte que su coste
de metadatos queda excluido. Los informes conservan también la separación entre
versiones vigentes, antiguas y partes multipart.

**No es una fotografía atómica de S3.** El recorrido usa varias peticiones y no bloquea
las escrituras de la suite ni las de otros clientes. Una carga puede completarse
entre el listado de versiones y el de cargas; una versión puede desaparecer después
de contarse, o crearse antes de un cursor ya recorrido. Puede haber omisiones o doble
representación temporal de datos. Un recorrido terminado no elimina esa limitación.

Si `ListParts` devuelve `NoSuchUpload`, esa carga queda como no inspeccionada, no como
una carga de cero bytes. Se conservan sus partes observadas anteriormente y se muestra
la cobertura incompleta. Otros errores de permisos/red detienen el recorrido. Un
inventario reanudado tras mucho tiempo sigue siendo el mismo intervalo de observación;
para una medida reciente hay que iniciar uno nuevo.

La API distingue `listing_finished`, `upload_inspection_complete` y
`storage_configuration_matches`. Siempre devuelve `consistent_snapshot=false` y
`quota_enforced=false`. No estima ni promete espacio disponible en el disco o en el
proveedor. No mide PostgreSQL, Redis, WAL, metadatos internos, réplica/erasure coding,
objetos ajenos al prefijo del espacio, almacenamiento externo de backups ni factura.

No comprueba checksums ni integridad de documentos. No sustituye una copia recuperable,
una política de retención ni el ensayo de restauración. Un recuento de cero, incluso
terminado, **no autoriza borrar originales, liberar reservas ni admitir gasto de IA**.

## Implementación y reanudación

- Migración `0047`: `storage_scans` y `storage_inventory_items`, RLS tenant-only y
  relaciones compuestas con los trabajos y los inventarios del mismo espacio.
- `POST /api/storage/inventories`: identidad de solicitud estable y motivo, rechazo
  de parámetros ajenos como bucket/prefijo y un único inventario activo por espacio.
  Solo guarda intención, Job y outbox en la transacción de la petición; no llama a S3.
- `GET /api/storage/inventories` pagina el historial de 20 en 20. `GET /{id}` devuelve
  agregados del inventario propio; los totales son privados al espacio aunque incluyan
  objetos publicados.
- `POST /{id}/retry` conserva el trabajo e incrementa la generación de envío, después
  de comprobar el bloqueo real del trabajador. Un estado de error por sí solo no
  demuestra que terminó el intento anterior. `POST /{id}/cancel` no elimina entradas.
- El trabajador `inventory_storage`, registrado en ARQ y outbox, lee páginas de hasta
  100 entradas: versiones de ambos buckets, cargas de ambos buckets y partes de cada
  carga pendiente. No acumula la colección completa en memoria.
- Cada página y su siguiente cursor se confirman juntos, reaplicando el contexto RLS.
  Las identidades incluyen inventario, bucket, clave y versión o carga/parte. Una
  identidad repetida en una página posterior falla: no se ignora ni suma de nuevo.
- Se comprueban destino, prefijo, truncamiento, tamaños, identificadores y cursores.
  No hay fallback que interprete una respuesta incompleta o no soportada como vacía.
  Los nombres URL-encoded se decodifican una sola vez; no se interpretan como rutas
  locales ni se normalizan componentes `..`, signos `+` o porcentajes literales.

El cliente debe admitir `ListObjectVersions`, `ListMultipartUploads` y `ListParts`,
incluida su paginación. Se necesitan los permisos S3 correspondientes de listar
versiones, cargas multipart y sus partes. Un cliente restringido a `ListObjectsV2` no
puede acreditar esta cobertura; se muestra un inventario incompleto.

Al restaurar una copia `0047`/`0048`, los informes del origen se conservan **invalidados**:
los identificadores de versiones, las partes pendientes y la historia S3 del origen
no se reconstruyen con la copia actual. No se permite reanudar sus cursores contra el
destino. Se debe crear otro inventario tras revisar la instalación restaurada.

## Relación con la cuota de escrituras `0048`

Este inventario no se utiliza para conceder espacio. `0048` implementa un registro
duradero de reservas/recibos, una línea base nueva con pausa de escrituras y la reserva
de espacio antes de pagar IA. Sus trabajos no se listan ni controlan desde las rutas
del inventario observacional: el bloqueo se gestiona exclusivamente desde el panel de cuota.

Siguen pendientes validación de escala/concurrencia/recuperación en PostgreSQL/S3 reales
y resolución segura de multipart y recibos huérfanos que no puedan acreditarse con una
lectura. No se supone espacio liberado ante un resultado incierto. Detalle, configuración
y límites: [Cuota de almacenamiento](CUOTA_ALMACENAMIENTO.md).

## Verificación de esta entrega

Pasan **170 pruebas de backend y 179 subtests**, **16 pruebas Playwright con API
simulada**, **18 del instalador/Compose**, typecheck y build del frontend. El bundle
principal queda en 469,45 kB; el panel se carga por separado. Se mantiene el aviso
previo del import estático/dinámico de linkage y la deprecación Starlette 413.

La nueva suite offline aporta 22 pruebas y 37 subtests, incluida una comprobación del
contrato boto3 con Stubber, sin red. Se genera correctamente SQL offline hasta el
único head `0047`; 189 rutas OpenAPI y 14 tareas duraderas registradas.

Las pruebas nuevas de integración cubren inventario con/sin versionado, copias,
objetos huérfanos, multipart, pérdida de conexión, paginación repetida, reanudación,
bloqueo de trabajador, cancelación, restauración, roles y aislamiento RLS de las dos
tablas. **Están preparadas, no ejecutadas aquí:** Docker devuelve permiso denegado
en su socket. Solo deben ejecutarse contra una BD desechable autorizada y buckets
`gensuite-test-*`/`gensuite-ci-*`, nunca contra la colección personal.

No se ha aplicado `0047` a datos reales, realizado un inventario real ni acreditado
preparación para producción. El resto del [plan de implementación](IMPLEMENTACION_INVESTIGACION.md)
sigue abierto.
