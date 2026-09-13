# Limpieza explícita de temporales de importación

Entrega `0046`. Permite descartar una subida abandonada o retirar los fragmentos duplicados
de una colección ya completada. Conserva Docker Compose, sin servicios ni dependencias
adicionales. **No se han ejecutado limpiezas sobre los datos de la instalación personal.**

## Qué puede eliminar

Solo los fragmentos del prefijo privado de una sesión, con esta forma exacta:

```text
{tenant UUID}/ingestion/{session UUID}/{file UUID}/{offset de 16 dígitos}-{SHA256}
```

Incluye las versiones antiguas y los marcadores de borrado de esos fragmentos. No elimina
el manifiesto de investigación, libros, páginas, originales ya ensamblados, PDFs, BagIt,
fuentes, citas ni objetos de otras sesiones. No utiliza el borrado genérico de prefijos.
Las claves ajenas o con estructura desconocida detienen la limpieza.

En una carga incompleta esos temporales pueden ser la única copia recibida por el servidor.
**Antes de descartarla, debes conservar y comprobar los originales locales y sus copias.**
La carga cerrada no vuelve a admitir manifiestos, subidas ni verificación. Para importarla
otra vez hace falta una sesión nueva; no se oculta ni borra su historial anterior.

## Uso desde la biblioteca

1. Abre la importación masiva y selecciona la sesión guardada. El filtro de 30 días muestra
   cargas sin actividad conocida; **no autoriza ni dispara ningún borrado automático**.
2. Pausa el envío del navegador. Si hay un trabajo de verificación en marcha, termínalo
   o solicita su cancelación y espera a que el trabajador deje de poseer su bloqueo real.
3. Abre «Liberar temporales de esta sesión (administración)». Introduce el motivo y pide
   la comparación: sesión, número de archivos/libros publicados, bytes recibidos y efecto
   sobre la reserva de admisión. Las versiones antiguas pueden ocupar más que lo recibido
   en el manifiesto; el trabajo inventariará todas después de cerrar el espacio de subida.
4. Confirma por separado que conservas los originales locales y que autorizas eliminar
   permanentemente solo los temporales y sus versiones. La edición del motivo invalida
   la comparación. Una modificación concurrente de la sesión devuelve conflicto.
5. Confirma el cierre. La sesión pasa a `cleaning` y se encola un trabajo duradero, sin
   eliminar objetos dentro de la petición HTTP. Solo administración puede aprobarlo.
6. Consulta fases, inventario, versiones confirmadas ausentes y errores. Si hay un fallo
   transitorio, reintenta **la misma limpieza autorizada**: no se reinventa el alcance ni
   se repite el procesamiento de los libros.

Al terminar, una colección completada sigue `completed`; una carga descartada pasa a
`discarded`. En ambos casos los libros que ya estaban publicados siguen accesibles.
La operación es irreversible para los temporales eliminados; no hay una deshacer de la
limpieza. El cierre de la ventana de importación normal, en cambio, conserva la sesión.

## Fases y garantías de implementación

1. **Aprobación y cierre.** Se bloquean Tenant y sesión, y se comprueba el bloqueo asesor
   PostgreSQL del trabajo de importación. Un estado aparentemente obsoleto no basta para
   afirmar que el trabajador ha parado. Intención de cola, aprobación y cierre se confirman
   en una sola transacción. La misma aprobación es idempotente; no crea otro trabajo.
2. **Comprobar originales conservados.** Se releen en bloques de 1 MiB, comprobando tamaño,
   SHA-256 y ruta determinista. En colecciones completadas se exige cada libro y original
   verificado. En descartes también se verifican los originales que ya constan ensamblados.
   Si falla esta fase no se eliminan fragmentos. No se relee el corpus completo en memoria.
3. **Inventariar versiones.** Se guardan páginas de hasta 100 versiones con cursor, sin
   borrar mientras el manifiesto esté incompleto. Cada fila identifica exactamente clave,
   versión y tamaño; la ruta se valida de nuevo al eliminar. Tras completar el inventario
   se retiran las referencias operativas a fragmentos de `ingest_files.parts`; sus claves
   y recibos de eliminación quedan en el inventario de limpieza, no como originales requeridos.
4. **Eliminar versiones exactas.** Cada confirmación se persiste. Si el proceso pierde una
   respuesta o muere después de eliminar, el reintento usa el mismo identificador de versión.
   No hay una operación que borre todo un bucket o prefijo sin inventario.
5. **Verificar que no queda staging.** Una nueva lectura debe acreditar el bucket, prefijo,
   fin de paginación y ausencia de versiones. Si aparecen objetos fuera del manifiesto no
   se amplía automáticamente el borrado ni se libera la reserva; se requiere revisión.

Los checkpoints reaplican RLS tras cada commit; las dos tablas nuevas son privadas por
tenant y tienen claves foráneas compuestas que impiden utilizar padres de otro espacio.
El trabajador está registrado en el outbox y en ARQ. Una cancelación detiene nuevos pasos
al comprobarse, pero no puede retirar una operación S3 que ya está en curso. La autorización
original no cambia de alcance al reintentar.

## Almacenamiento versionado y permisos

En S3, borrar una clave sin versión puede insertar un marcador sin liberar el contenido
anterior; eliminar una versión específica es permanente. El código siempre envía su
`VersionId`, incluido el valor literal `null` para objetos no versionados. No pide saltarse
Object Lock, retención ni MFA Delete. Un bloqueo del proveedor detiene el trabajo y conserva
su estado. [Contrato oficial de DeleteObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html).

El inventario usa versiones, marcadores y ambos cursores de paginación, no solo la lista
de objetos actuales. El backend necesita permisos para listar versiones y eliminar las
versiones de **ese staging privado**, además de leer los originales para verificarlos.
No debe ampliarse a los originales ni al backup el permiso de eliminación de temporales.
[Contrato oficial de ListObjectVersions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectVersions.html).

Los proveedores compatibles deben respetar estos contratos. No hay fallback a una
eliminación menos precisa si no los soportan. Los bytes mostrados corresponden a versiones
inventariadas y confirmadas ausentes, no a una certificación del espacio físico liberado
por el proveedor, su replicación o su factura.

## Cuotas: alcance deliberadamente explícito

`INGEST_TENANT_QUOTA_BYTES` sigue siendo una **cuota de admisión de originales de nuevas
sesiones**, no una cuota física global. La aprobación o un trabajo fallido no libera nada.
Solo después de completar la limpieza:

- Una sesión `manifest`/`uploading` sin ningún trabajo de procesamiento libera su reserva.
- Si ya había procesamiento, o su estado previo no prueba que nunca empezó, se conserva
  la reserva completa: un original puede haberse escrito antes de su checkpoint de BD.
- Una colección completada conserva la reserva de sus originales, aunque retire temporales.

No se pone `reserved_bytes=0` ni se altera el manifiesto declarado. La admisión suma su
reserva original o el importe retenido de una limpieza terminada, bajo el bloqueo de Tenant.
La medición y cuota física que incluyan cargas antiguas, versiones, copias, exportaciones
y respuestas de IA **siguen pendientes**, al igual que la retención de originales/papelera.

## API, migración y recuperación

- `GET /api/documents/ingestion?inactive_days=30`: candidatas por actividad, sin mutación.
- `GET /api/documents/ingestion/quota`: reserva de admisión y límite; devuelve
  `physical_storage_quota: false` para evitar confundir ambas medidas.
- `POST /api/documents/ingestion/{id}/cleanup/preview`: comparación sin escrituras ni S3.
- `POST /api/documents/ingestion/{id}/cleanup`: `preview_hash`, `reason`,
  `authorize_permanent_staging_deletion: true` y `local_originals_confirmed: true` estrictos.
- `GET /api/documents/ingestion/{id}/cleanup`: estado, responsable, motivo y contadores;
  nunca expone claves o rutas del almacenamiento al navegador.
- `POST /api/documents/ingestion/{id}/cleanup/retry`: reentrega el mismo Job y outbox.

La migración aditiva `0046` crea `ingest_cleanups` e `ingest_cleanup_objects` con RLS, sin
backfill ni eliminación de datos. Actualiza API y worker conjuntamente después de ensayar
la migración en un entorno desechable. El recuperador admite esquemas `0044` a `0046`.
Una restauración crea versiones S3 nuevas: la cuarentena inicia otra época de inventario
para limpiezas pendientes, manteniendo los recibos antiguos y sin activarlas automáticamente.
Una copia de instalación conserva los objetos actuales, no las versiones históricas S3.

## Verificación y límites de esta entrega

Pasan **148 pruebas backend y 142 subtests**, **14 Playwright** con API simulada, **18 del
instalador**, typecheck/build, SQL offline `0001`–`0046` y registro de 13 tareas duraderas.
Hay 186 rutas OpenAPI. El bundle inicial permanece en 469,26 kB; persiste el aviso previo
de import estático/dinámico de linkage.

Se recopilaron **120 pruebas de integración, no ejecutadas**. Ocho nuevas cubren RLS,
padres cruzados, PostgreSQL+MinIO con/sin versiones, conservación de originales, cierre
de subida, cuota, confirmación obsoleta, bloqueo real y reintento tras una respuesta de
eliminación perdida. Docker sigue denegando acceso al socket en el entorno de trabajo.
Faltan ejecución real, pruebas de fallos/retención del proveedor y escala de 100.000 archivos.
Esta entrega no completa el plan general ni habilita por sí sola la salida a producción.
