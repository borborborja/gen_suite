# Cuota de almacenamiento y recuento protegido

Entrega `0048`: admisión de escrituras de la aplicación por espacio, con reservas
duraderas antes de enviar bytes a S3 y un recuento protegido para datos existentes.
**La integración real ha detectado un bloqueo:** MinIO comunitario puede ocultar las
partes multipart al listar por prefijo y hacer que la cuota subcuente. No iniciar
recuentos de colecciones reales ni usar esta cuota como garantía de capacidad hasta
resolverlo. Se mantienen fallando las pruebas que lo acreditan; no se ha desplegado
en la colección personal. [Estado de validación real](ESTADO_VALIDACION_REAL.md).

## Qué limita y qué no

`STORAGE_TENANT_QUOTA_BYTES` limita por espacio los **bytes de contenido conservados
o reservados**, no el uso físico del disco. El valor inicial es 1 TiB
(`1099511627776` bytes). Es un límite de configuración común a todos los espacios,
no una reserva de capacidad física para cada uno. No hay botón para desactivarlo.

La admisión comprueba, bajo el bloqueo de la fila de cuota:

`consumo conservador + reservas pendientes + nueva escritura <= límite`

Incluye los objetos del prefijo propio en los buckets privado y público: originales,
imágenes, fragmentos, manifiestos, PDF/ZIP, copias de publicación y respuestas de IA.
El recuento incluye versiones antiguas, objetos sin referencia en PostgreSQL y partes
multipart incompletas. No cuenta como propios los documentos públicos de otros espacios.

No mide réplicas, erasure coding, índices/metadatos S3, marcadores de borrado en disco,
PostgreSQL/pgvector, Redis, archivos temporales locales, copias externas ni facturación.
Debe haber monitorización y margen físico adicional en el host/proveedor, también para
el consumo agregado de todos los espacios. Una cuota no sustituye una copia restaurable.

La garantía presupone **API y workers actualizados conjuntamente y ausencia de otros
escritores en los buckets de la aplicación**. Un cliente S3 externo o una versión antigua
puede introducir bytes sin reservarlos. Ni esta cuota ni la confirmación de la interfaz
bloquean universalmente esos clientes. Un proveedor S3 incompatible falla cerrado.

La cuota de importación `INGEST_TENANT_QUOTA_BYTES` sigue siendo una admisión separada
del volumen de originales. Superar cualquiera de las dos detiene la operación: disponer
de reserva de importación no garantiza espacio para todos sus fragmentos y derivados.

## Activación sobre una colección existente

1. Validar primero la migración `0048`, RLS, concurrencia y flujos completos en servicios
   desechables. Preparar una copia restaurable antes de actualizar la instalación personal.
2. Actualizar API y worker conjuntamente mediante Compose, sin mantener escritores de
   versiones anteriores. Revisar el límite y los permisos de los dos buckets.
3. En **Ajustes > Cuota de almacenamiento**, indicar el motivo y confirmar la pausa de
   nuevas escrituras y que se han detenido los otros clientes que escriben en S3.
   Modificar el motivo invalida la confirmación en la interfaz.
4. **Recontar y actualizar la cuota** crea un trabajo duradero y activa `draining`:
   se rechazan nuevas escrituras, incluso borrados de cero bytes, y nuevas llamadas
   de IA alojada que requieren reservar su respuesta. Las reservas previas sí pueden
   terminar; las lecturas y la revisión de evidencias siguen disponibles.
5. Cuando no quedan recibos pendientes ni bytes reservados, el worker pasa a `counting`.
   Recorre versiones y partes con cursores persistentes. Solo publica la nueva línea
   base si terminó la inspección, el destino coincide y el bloqueo sigue siendo suyo.
6. Si termina correctamente, pasa a `ready`. El consumo puede superar el límite
   configurado: se muestra el valor completo y se impiden nuevas escrituras, sin borrar
   originales para encajarlo. Si falla, el bloqueo permanece y la interfaz permite
   **reintentar el mismo trabajo** o **cancelar conservando la cuota anterior**.

Cancelar no concede espacio, no descarta recibos ni convierte el informe parcial en una
línea base. Un worker anterior no puede publicar el recuento después de esa cancelación.
Los controles del inventario observacional no acceden a estos trabajos; la cancelación
genérica de un trabajo activo remite al panel de cuota para no dejar un bloqueo oculto.

No se aplica un backfill al migrar ni se asigna cero a objetos antiguos. Un espacio
nuevo se inicializa durante su primera admisión solo si cuatro consultas acotadas
(versiones y multipart en ambos buckets) acreditan que su prefijo está vacío. Si ya
hay datos, exige este recuento; si S3 no permite comprobarlos, no admite la escritura.
Consultar el estado por GET no inicializa ni modifica la cuota.

El [inventario observacional `0047`](INVENTARIO_ALMACENAMIENTO.md) permanece separado:
sirve para investigar el almacenamiento sin pausarlo, pero nunca se activa retrospectivamente
como línea base. El recuento protegido siempre crea un inventario nuevo.

## Escrituras, copias e incertidumbre

Cada objeto obtiene un `storage_writes` independiente de la transacción del documento.
Se confirma el estado `sending` antes de enviar a S3 y se guarda su identificador en los
metadatos del objeto. Un HEAD verifica tamaño e identidad antes de pasar a `succeeded`.
Si la transacción del documento falla después, el objeto huérfano sigue contabilizado.

No se descuenta un objeto anterior al sobrescribir ni se concede espacio al recibir
un ACK de borrado: puede existir una versión retenida o un mero marcador. Esto sobreestima
el consumo en buckets sin versionado; un **nuevo recuento protegido completo** puede
reducirlo. La limpieza de staging conserva sus autorizaciones y recibos exactos de `0046`.

El cliente S3 de la aplicación usa `total_max_attempts=1`: una tentativa total, incluida
la inicial. Evita que el SDK repita silenciosamente una escritura cuyo ACK se perdió;
no promete exactamente una ejecución de la infraestructura remota. El cliente separado
de backup conserva su contrato propio. [Configuración de reintentos boto3](https://docs.aws.amazon.com/boto3/latest/guide/retries.html).

Las cargas grandes usan partes acotadas (normalmente 16 MiB, hasta 256 MiB según tamaño),
con un máximo de 10.000 recibos y `UploadId` persistido antes de enviar las partes. No
leen el archivo completo en memoria ni envían más bytes que su tamaño reservado.
Las copias pequeñas leen hasta 16 MiB con ETag y versión observados antes de escribir;
las grandes copian rangos explícitos del origen fijado. S3 solo admite rangos de
`UploadPartCopy` si el origen supera 5 MB, por lo que no se usa esa operación para las
copias pequeñas. [Contrato de UploadPartCopy](https://docs.aws.amazon.com/AmazonS3/latest/API/API_UploadPartCopy.html).

Copiar o borrar prefijos procesa páginas de hasta 100 claves, comprueba su pertenencia
y exige avance ordenado; no conserva todas las claves en RAM ni admite páginas repetidas.
Se usa el contrato de buckets S3 de propósito general, no S3 Express/directory buckets.
[Orden y paginación de ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html).

Ante timeout, cancelación o fallo al guardar el recibo, `sending`/`unknown` mantienen
toda la reserva. **Verificar recibo sin repetir escritura** solo lee S3: acredita el
identificador y tamaño del objeto, o la ausencia de la versión exacta en una eliminación.
Un objeto ausente no demuestra que un PUT todavía en vuelo no pueda aparecer después.
Los listados de recibos no exponen claves, buckets, credenciales ni identificadores multipart.
El panel pagina de 20 en 20 y permite volver al inicio cuando las operaciones finalizadas
desplazan el listado; no oculta permanentemente los recibos posteriores a los veinte primeros.

## Reservar el resultado antes de pagar IA

Una llamada nueva de IA alojada reserva **16 MiB para su respuesta** en la misma
transacción PostgreSQL que su reserva económica. Si no caben, no se envía al proveedor
ni queda confirmada la reserva monetaria. La respuesta utiliza ese recibo preasignado;
al guardarse y comprobarse, solo se contabilizan sus bytes reales.

Una respuesta ya pagada y reservada puede guardarse durante `draining`. La reserva se
conserva aunque cambie el mes o se pause la investigación. Los resultados ya guardados
pueden releerse sin pagar otra llamada. Un fallo del proveedor antes de iniciar S3
cancela solo la reserva de almacenamiento aún sin usar, no su posible coste económico.

Esto no garantiza recibir una respuesta si muere el proceso, si el proveedor devuelve
más de 16 MiB o si S3 deja de estar disponible. Se conservan los estados inciertos y
se remite a la [conciliación económica](CONTROL_GASTO_IA.md), sin repetir llamadas de pago.

## Límites operativos todavía pendientes

- Un proceso muerto antes de empezar S3 puede dejar un recibo `reserved` sin consumidor.
  `0049` + `0050` añaden [cancelación auditada](CANCELACION_RESERVAS.md) de reservas no
  iniciadas, sin borrar objetos. Para IA exige guarda nueva, conciliación previa y
  aceptación de posibles respuestas tardías; los recibos IA históricos sin guarda
  siguen excluidos. Conciliar dinero no libera automáticamente esa reserva de bytes.
- Un multipart interrumpido puede conservar partes y `UploadId`, sin objeto final cuya
  identidad permita liquidarlo. No se aborta automáticamente ni se supone que el espacio
  está libre. También puede perderse el ACK de crear la carga antes de guardar su ID.
- Un borrado sin versión de resultado incierto no se resuelve por ausencia observada.
  La excepción de reintento exacto de staging solo vale para el mismo trabajo serializado
  y su sesión de importación cerrada permanentemente, nunca para claves documentales reutilizables.
- Estos casos pueden bloquear un recuento hasta disponer de un procedimiento de resolución
  seguro. Se puede cancelar el recuento para recuperar la admisión anterior, manteniendo
  reservas y consumo; **no editar a mano los contadores ni borrar recibos para desbloquear**.
- Falta validar el ciclo completo en PostgreSQL/S3 reales, incluyendo pérdida de ACK,
  parada de procesos, cuotas concurrentes, latencia de admisión y escala de 100.000 páginas.
  Esta entrega no cierra por sí sola el bloque de almacenamiento del plan.

## Configuración, esquema y recuperación

La instalación sigue mediante Compose: no añade dependencias ni servicios. Configurar
`STORAGE_TENANT_QUOTA_BYTES` a través del entorno habitual y mantener el mismo valor en
API y worker. Reducirlo no elimina datos; aumentar el número no crea capacidad física.
No ejecutar una versión anterior que escriba fuera del registro sobre una cuota activa.

Además de leer/escribir objetos, el cliente necesita listar versiones y cargas multipart
en ambos buckets, listar partes y leer versiones exactas. Faltar permisos no equivale
a un prefijo vacío. No conceder permisos de borrado adicionales solo para hacer un recuento.

La migración aditiva `0048` crea `storage_quotas` y `storage_writes`, ambas con RLS
tenant-only, restricciones e identidades compuestas. Vincula `ai_calls.response_write_id`
sin modificar llamadas históricas ni migraciones anteriores. Hay pruebas de lectura,
escritura y padres cruzados entre espacios preparadas para PostgreSQL real.

La [recuperación completa](RECUPERACION_COMPLETA.md) admite `0048`: invalida los inventarios
del origen, pone recibos de escrituras pendientes en cuarentena y deja la cuota
`uninitialized`, sin línea base del destino. Conserva el historial; no supone que las
versiones o multipart originales hayan sido restaurados. Exige otro recuento protegido
tras revisar el destino. Los importes inciertos de IA siguen pendientes de conciliación.

## Verificación

Pasan **196 pruebas backend y 208 subtests**, **19 Playwright con API simulada**, **18
del instalador/Compose**, typecheck y build. El SQL offline llega al único head `0048`,
con 194 rutas OpenAPI y 15 tareas duraderas registradas. Se recopilaron **136 tests de
integración, sin ejecutarlos**: Docker deniega acceso al socket del entorno.

La suite nueva de cuota aporta 26 pruebas offline y 29 subtests; las pruebas preparadas
para PostgreSQL/S3 cubren cuotas concurrentes, datos antiguos y versiones, copias,
pérdida de ACK, drenaje de reservas, cancelación y separación de controles. Las dos
tablas nuevas tienen casos de RLS y claves foráneas compuestas entre espacios.

Resultados locales y pendientes de integración en el
[estado del plan](IMPLEMENTACION_INVESTIGACION.md#verificación). Las pruebas de navegador
usan API simulada y las pruebas offline usan datos sintéticos, sin S3, BD ni IA de pago.
No se han aplicado migraciones, inventariado, borrado ni restaurado datos personales.
