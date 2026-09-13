# Investigación y preservación: entrega inicial

Esta entrega implementa una parte del plan integral. **No acredita preparación para
producción ni autoriza importar el único ejemplar de una colección.** No se han
ejecutado migraciones contra datos reales ni llamadas de IA de pago.

**Bloqueo actual:** las pruebas con servicios reales detectaron que el MinIO
comunitario incluido subcuenta multipart y tiene un problema de seguridad independiente.
No desplegar ni usar sus recuentos como garantía de cuota. Véase el
[estado de validación real](ESTADO_VALIDACION_REAL.md), incluidas las correcciones `0051`.

## Disponible en el código

- Importación revisada GEDCOM 7/GEDZIP: original privado comprobado, informe inmutable,
  confirmación y append atómico sin fusionar, fuentes con múltiples documentos y
  conservación de PDF/imágenes. Cancelación, reintento explícito y RLS. Esquema `0053`;
  no certifica compatibilidad universal ni restauración nativa.
  [Perfil y límites](IMPORTACION_ARBOL.md).
- Exportaciones privadas en segundo plano de GEDCOM 5.5.1, GEDCOM 7.0 y GEDZIP con
  originales propios, instantánea PostgreSQL consistente, comprobación de checksums,
  cancelación e informe inmutable. Nuevo esquema `0052`, sin backfill ni dependencias.
  Paquete nativo por espacio aún pendiente; importación inicial en el punto anterior.
  [Alcance y límites](EXPORTACION_ARBOL.md).
- Intercambio GEDCOM 5.5.1 con fuentes, repositorios y citas en su destino exacto;
  importación bibliográfica, notas personales y filiación estándar. Revisión previa de
  pérdidas con huellas de archivo/informe, rechazo de cambios y referencias antiguas
  sin resolver explícitas. No sustituye el importador revisado 7/GEDZIP ni el paquete nativo por espacio.
  Véase [intercambio y límites](INTERCAMBIO_GEDCOM.md).
- Intenciones de envío a ARQ guardadas en la misma transacción que el trabajo. El
  dispatcher reintenta ante fallos de Redis; las funciones registradas serializan la
  reentrega de un mismo trabajo mediante un bloqueo de sesión de PostgreSQL.
- Transcripción: se respetan las revisiones corregidas, se registran consumos por
  llamada mediante el nuevo registro de admisión y se distingue finalización parcial de éxito. No se promete
  exactamente una llamada a un proveedor si el proceso muere antes de guardar su respuesta.
- Correcciones humanas y reconciliación crean revisiones nuevas. Se conserva el
  texto anterior, se invalidan actas/candidatos pendientes dependientes y no se
  modifican las conclusiones aceptadas del árbol. El visor muestra el historial.
- La reconciliación HTTP exige `expected_versions` para no aceptar versiones no
  revisadas. `keep_history=false` ya no elimina pruebas. La mezcla LLM inline se
  rechaza hasta disponer de una ejecución presupuestada; queda la mezcla por frecuencia.
- Extracción: validación estricta, registros de páginas sin actas en el modo síncrono,
  y en batch, conservación de las mitades al unir actas y resultados parciales explícitos.
  Las respuestas ausentes del lote se cuentan como fallos; se rechazan resultados de
  revisiones que dejaron de estar activas mientras el proveedor trabajaba.
- Extracción y embeddings: las transiciones no sobrescriben estados terminales y
  comprueban la cancelación antes de comenzar más llamadas. Una solicitud ya enviada
  puede terminar y facturarse; no equivale a cancelar el procesamiento remoto del proveedor.
  El consumo de embeddings se guarda por llamada y un lote incompleto no se marca como éxito.
  El siguiente trabajo de embeddings se programa mediante outbox junto al cierre de la extracción.
- Re-rasterizar crea otro documento derivado, sin eliminar las páginas ni citaciones
  del original. Se guarda la configuración del trabajo y el origen físico de cada vista.
- Imágenes subidas: formato verificado, nombre original y SHA-256 por página; manifest
  de originales. Rasterización iterativa con límite de dimensiones y WebP sin pérdida.
- PDF de consulta generado en segundo plano: facsímil y anexo textual Unicode. No
  simula posiciones de palabras ni se anuncia como PDF/A. No consume IA al exportar.
- Perfil Compose opcional [PDF/A-2b](PDFA.md), con dependencias autorizadas:
  OCRmyPDF sin OCR/IA, veraPDF independiente, comprobación de páginas/texto,
  informe privado ligado a checksums, descarga y aviso de versión obsoleta. Worker
  separado con heartbeat, límites y cancelación de procesos. Pasa prueba sintética
  con binarios reales y recorrido HTTP→PostgreSQL→ARQ→S3 aislado; faltan imagen/Compose
  y validación con libros representativos. Véase [validación real](ESTADO_VALIDACION_REAL.md).
- Paquete BagIt ZIP **por documento**, con imágenes, revisiones, actas, menciones,
  fuentes, repositorios y citas; checksums de contenido y archivos de control.
- Exportaciones privadas, versionadas y descargables por el backend, con HTTP Range.
  Los navegadores compatibles escriben directamente a disco; el fallback de memoria
  se limita a 200 MB. Se avisa si cambió la transcripción tras generar el archivo.
- Biblioteca paginada, selección de carpetas, agrupación por carpeta y orden natural.
  La nueva **Importación masiva / reanudar colección** conserva manifiestos y fragmentos
  de 8 MiB entre sesiones; el navegador vuelve a solicitar la carpeta por seguridad y
  comprueba los fragmentos existentes antes de omitir su envío. El importador de servidor
  usa la misma API, admite montaje de solo lectura mediante Compose y no necesita claves S3.
  Los libros solo se publican privados cuando todos sus originales están verificados.
  La subida simple anterior conserva sus límites. Véase [Importación masiva](IMPORTACION_MASIVA.md).
- Las rutas antiguas de borrado y descarte irreversible devuelven 409 antes de tocar
  almacenamiento. Es una protección temporal, no una implementación de papelera.
- Las pruebas destructivas requieren una BD `gensuite_test_*` o `gensuite_ci` y
  `GEN_SUITE_ALLOW_TEST_TRUNCATE=yes`. Las comprobaciones de `backend/checks` no requieren BD.
- Admisión de IA alojada por llamada, con reservas duraderas en micro-USD, respuestas
  privadas recuperables y bloqueo ante incertidumbre. Ajustes permite confirmar tarifas,
  consultar reservas, recuperar resultados y conciliar con evidencia. Las llamadas pagadas
  no tienen reintentos SDK ni fallback pagado silencioso. **Batch nuevo queda desactivado**
  hasta implementar su reserva/conciliación por solicitud. Los perfiles requieren confirmación
  explícita; `budget=null` ahora desactiva IA de pago. Véase [Control de gasto](CONTROL_GASTO_IA.md).
- Investigaciones `ResearchRun` con preparación sin gasto, etapas persistentes, inicio
  autorizado, pausa/reanudación, incidencias y cancelación sin borrado. Fijan perfiles al
  prepararse y aplican presupuesto de toda la investigación además del mensual del espacio.
  La represa conserva el mismo trabajo, sus respuestas verificadas y checkpoints; espera
  el bloqueo real del intento anterior. La lectura de índices ya no vacía resultados al
  reanudar. La pantalla de Investigación permite controlar y revisar estas ejecuciones.
  Véase [Investigaciones automáticas](INVESTIGACIONES_AUTOMATICAS.md).
- Índices vectoriales por generaciones: reconstrucción sin vaciar vectores antiguos,
  activación y vuelta atrás auditadas, consulta con el modelo del índice y exclusión de
  evidencias corregidas o retiradas. Captura paginada, perfil fijado, procedencia por
  elemento/consulta y opción explícita de indexar documentos públicos accesibles.
  Véase [Índices generacionales](INDICES_GENERACIONALES.md).
- Recuperación de instalación completa por CLI y overlay Compose opcional: snapshot
  PostgreSQL, objetos de ambos buckets, comprobación de referencias/checksums, transferencia
  externa con lectura de verificación y restauración solo en otro clúster/buckets vacíos,
  con cuarentena de ejecución y rol de aplicación sin login. No es la exportación portable
  por espacio ni acredita un ensayo real. Véase [Recuperación completa](RECUPERACION_COMPLETA.md).

- Inventario de almacenamiento en Ajustes: recorrido reanudable por ambos buckets del
  espacio, versiones antiguas, copias, objetos sin referencias y partes multipart.
  Guarda cursores y observaciones sin modificar S3; distingue cobertura incompleta e
  invalida informes del origen tras restaurar. No es por sí mismo una cuota ni una
  fotografía atómica. Véase [Inventario de almacenamiento](INVENTARIO_ALMACENAMIENTO.md).
- Cuota de contenido S3 por espacio con reservas duraderas antes de cada escritura,
  recuento nuevo bajo pausa de escrituras y espacio preasignado a la respuesta antes de
  admitir IA pagada. Cuenta originales/derivados/copias y conserva resultados inciertos;
  no descuenta por sobrescritura o ACK de borrado. Mantiene Compose. Falta validación real
  y resolución operativa de algunos multipart/recibos huérfanos; no es una cuota del disco.
  Véase [Cuota de almacenamiento](CUOTA_ALMACENAMIENTO.md).
- Cancelación auditada de reservas S3 nunca iniciadas, con revisión y confirmación,
  guarda de respuestas IA nuevas, comprobación concurrente e historial paginado. No
  libera resultados ya enviados o inciertos. Para IA exige conciliación y aceptación
  separada de posibles respuestas tardías. Véase [Cancelación de reservas](CANCELACION_RESERVAS.md).

## Esquema y despliegue

Migraciones nuevas, aditivas y sin backfill: `0038` (outbox), `0039` (procedencia y
revisiones) y `0040` (exportaciones y checkpoints). Las tres tablas nuevas tienen RLS
tenant-only y casos de prueba de lectura/escritura cruzada.
`0041` añade sesiones, libros y archivos de importación, con RLS tenant-only y claves
foráneas compuestas que también impiden vincular un padre de otro espacio.
`0042` añade el registro `ai_calls`, con RLS tenant-only, sin recalcular consumos antiguos.
`0043` añade investigaciones, etapas y eventos con RLS y relaciones compuestas entre
espacios; vincula llamadas a su investigación y versiona la reentrega de la cola.
`0044` añade generaciones, elementos vectoriales y eventos de índice con RLS tenant-only;
no borra ni atribuye un modelo a los vectores históricos sin procedencia.
`0045` añade autorizaciones económicas versionadas, sin modificar perfiles originales.
`0046` añade limpiezas explícitas de staging y recibos de versiones, con RLS y sin backfill.
`0047` añade inventarios observacionales y sus entradas, con RLS y relaciones compuestas,
sin modificar originales ni activar inventarios o cuotas automáticamente.
`0048` añade cuotas y recibos S3 con RLS y vínculo compuesto a respuestas de IA. No
inicializa datos antiguos a cero; el recuento protegido exige acción explícita.
`0049` añade auditoría de cancelaciones de reservas y capacidad de guarda IA nullable,
sin backfill. `0050` revoca los privilegios de edición heredados por defecto y añade
políticas restrictivas a esa auditoría; esta entrega requiere ambas migraciones.

`0051` amplía el contrato `documents.source_kind` con `resumable_upload` y la longitud
de `change_log.entity_type` a 64: las pruebas PostgreSQL reales detectaron que los
contratos anteriores impedían publicar importaciones y auditar conexiones Gramps.
No reescribe originales ni registros. Su downgrade rechaza datos incompatibles sin
truncarlos. `0052` añade exportaciones del árbol, con RLS, referencia compuesta al job
y permisos de resultado inmutable. `0053` añade originales e informes de importación
revisada y vínculos múltiples de documentos por fuente, sin backfill. La aplicación
actual requiere el head `0053`.

La instalación sigue siendo Compose. No se añaden dependencias al proyecto ni servicios
obligatorios nuevos. API y worker deben actualizarse conjuntamente después de validar
las migraciones en una instalación desechable y disponer de una copia restaurable.
No debe desplegarse el nuevo código sobre el esquema anterior.

## Pendiente antes de completar el plan

1. Validar la importación fragmentada completa de 100.000 imágenes con PostgreSQL y S3
   reales, no solo escaneo/planificación. `0046` implementa limpieza explícita y reanudable
   de temporales y liberación conservadora de reservas de cargas abandonadas; falta
   medirla a escala (los casos sintéticos PostgreSQL/S3 ya pasan). `0047` aporta el inventario observacional y `0048`
   implementa la cuota de contenido, línea base protegida, recibos y reserva de respuestas
   antes de pagar IA. `0049`/`0050` permiten cancelar de forma auditada reservas no iniciadas,
   con protección adicional para IA. Pasan sus casos PostgreSQL, pero fallan los inventarios
   multipart del MinIO incluido. Faltan compatibilidad S3 completa, tratamiento de recibos IA
   históricos sin guarda, multipart inciertos y borrados/PUT enviados sin resultado
   acreditable, que todavía pueden bloquear el recuento. Completar monitorización/capacidad física (BD, réplicas, temporales,
   backups y suma de espacios), que la cuota de contenido no representa.
2. Validar `ResearchRun` y sus etapas en PostgreSQL/ARQ/proveedor reales. La orquestación,
   pausas, presupuesto por investigación y congelación del perfil al preparar el plan ya
   están implementados. `0045` añade reautorización económica versionada sin cambiar la
   identidad de la ejecución; sus casos PostgreSQL reales pasan con proveedor simulado.
   Faltan el recorrido con proveedor autorizado, Batch gestionado
   y optimización acreditada de las cotas de tokens; no se promete un límite
   sobre la factura externa ni una ejecución exactamente una vez del proveedor.
3. Benchmark con manuscritos reales, geometría validada, pruebas por campo y perfiles
   lingüísticos. No hay métricas de precisión genealógica acreditadas todavía.
4. Ampliar a escala real los casos PostgreSQL de reindexación generacional y resultados
   trazables que ya pasan con datos sintéticos; medir recuperación, latencia y calidad genealógica con
   evidencia real. Completar mantenimiento/retención y cobertura después de cambios
   concurrentes del corpus. La corrección actual actualiza FTS; no dispara gasto de embeddings.
5. Ejecutar el perfil PDF/A ya implementado en Compose aislado y validar libros
   representativos, capacidad, cancelación y fidelidad visual. Las dependencias
   OCRmyPDF/veraPDF ya están autorizadas; no hace falta volver a solicitarlas.
   Completar exportación/restauración nativa **completa**, compatibilidad y auditoría
   de pérdidas de interoperabilidad. La reparación de bibliografía/citas de 5.5.1 y su
   auditoría inicial ya están implementadas; faltan cobertura exhaustiva, consumidores
   independientes, compatibilidad y escala de los formatos nuevos. La exportación
   e importación GEDCOM 7/GEDZIP iniciales ya están implementadas, con perfiles limitados.
   El BagIt por libro no es un backup completo.
6. Backfill controlado de originales históricos y papelera/retención; el backfill requiere
   aprobar la migración de datos. La copia conjunta PostgreSQL+objetos y el recuperador
   aislado pasan un ensayo completo con datos sintéticos en otro clúster y otro S3:
   8 objetos, 4 referencias, 213 comprobaciones RLS y cuarentena acreditada.
   Faltan ampliar fallos y medir escala/tiempos/espacio,
   custodia acreditada de claves, políticas del backup externo y retención de parciales.
7. Resolver las dos fallas de integración multipart y repetir PostgreSQL/RLS; completar
   Compose, E2E, concurrencia, escala y seguridad antes de beta por invitación.
   El [gate automático de publicación](GATE_PUBLICACION.md) ya exige CI y recuperación
   verdes para el SHA exacto de un push a main y fija ese SHA en el build. Falta ejecutarlo
   en GitHub con el commit integrado y verificar protecciones de rama/tag y permisos.
   Los casos de integración sintética ya ejecutados no sustituyen esos gates.

## Verificación

**Exportaciones del árbol (`0052`):** ver [validación vigente](ESTADO_VALIDACION_REAL.md)
y [alcance funcional](EXPORTACION_ARBOL.md). Se mantiene abierto el plan completo.

**Histórico — intercambio GEDCOM 5.5.1:** 242 unitarias, 28 pruebas de navegador con API simulada,
40 de scripts y typecheck/build correctos. La integración general termina con 150
correctas y 2 fallidas (multipart MinIO), sin omisiones; tras los ajustes finales pasan
los 9 casos de árbol/fuentes repetidos. Esa entrega no completaba GEDCOM 7/GEDZIP ni el formato
nativo por espacio. [Informe y límites](INTERCAMBIO_GEDCOM.md).

**Gate de publicación:** pasan 40 pruebas de scripts (20 del instalador y 20 del nuevo
verificador), 233 unitarias backend y typecheck; YAML de los tres workflows válido.
El gate está implementado, no ejecutado en GitHub. No se han publicado imágenes ni
cambiado protecciones remotas. Persisten los dos fallos de integración multipart.

**Recuperación completa sintética posterior:** pasa `checks/smoke_recovery.py` con
dos clústeres PostgreSQL y dos S3 independientes, además de la prueba de snapshot.
Se corrigen serialización del catálogo y equivalencia de casts tras `pg_restore`.
Pasan 233 unitarias/293 subtests, 20 del instalador y typecheck; se añade el workflow
`verify-recovery`, aún sin ejecutar aquí. [Ensayo y límites](RECUPERACION_COMPLETA.md).

**Verificación real más reciente (`0051`):** 145 pruebas de integración correctas y
2 fallidas por multipart MinIO, sin omisiones; 228 unitarias/271 subtests y 20 del
instalador correctos; typecheck/build, mappers y SQL offline correctos. PDF/A pasa el
recorrido real HTTP→outbox→ARQ→S3. Las migraciones se aplicaron solo al clúster temporal.
Detalle y límites: [Estado de validación real](ESTADO_VALIDACION_REAL.md).
Los recuentos siguientes son históricos y no se suman al resultado actual.

Comprobaciones anteriores a `0042`: 66 pruebas de backend y 30 subtests sin servicios externos,
4 pruebas del cliente de importación (incluida planificación de 100.000 imágenes y una
prueba Chromium de error/recarga/reanudación con API simulada), 17 del instalador,
typecheck y build del frontend, importación de API/worker, OpenAPI y generación SQL
offline hasta `0041`. La compilación del frontend avisa de un import estático/dinámico
ya existente en linkage, sin impedir el build.

La validación local de `0042` pasa 85 pruebas de backend y 60 subtests, 6 pruebas de
navegador con API simulada, 17 del instalador, typecheck/build y SQL offline hasta `0042`.
Los resultados actualizados se recogen en [Control de gasto](CONTROL_GASTO_IA.md); las pruebas
PostgreSQL/RLS añadidas están preparadas, no se confunden con los contratos simulados locales.

La entrega `0043` pasa **102 pruebas backend y 75 subtests**, **8 pruebas Playwright**
con API simulada, **17 pruebas del instalador**, typecheck/build, SQL offline hasta `0043`
y registro de las 11 funciones duraderas. Se recopilaron 99 tests de integración sin
ejecutarlos. Detalle y límites en [Investigaciones automáticas](INVESTIGACIONES_AUTOMATICAS.md).

La entrega `0044` pasa **115 pruebas backend y 86 subtests**, **10 pruebas Playwright**
con API simulada, **17 del instalador**, typecheck/build y SQL offline hasta `0044`.
Se recopilaron 104 tests de integración sin ejecutarlos. Se mantienen pendientes la
validación real y los demás bloques del plan: [Índices generacionales](INDICES_GENERACIONALES.md).

La entrega de recuperación, sin nueva migración, pasa **136 pruebas backend y 101
subtests**, **18 del instalador/Compose** y typecheck/build. Añade contratos de creación,
transferencia externa, verificación, guardas y cuarentena; no se confunden con un ensayo
real de recuperación. Detalle: [Recuperación completa](RECUPERACION_COMPLETA.md).

El recuento actual de `0045`, obtenido con pytest sin sumar suites históricas, es **131
pruebas backend y 120 subtests**, **12 Playwright** con API simulada y **18 del instalador**.
Pasan typecheck/build y SQL offline hasta `0045`, con 182 rutas OpenAPI; se recopilaron
112 tests de integración sin ejecutarlos. Incluye 16 pruebas offline y 7 de integración
nuevas de renovación económica. Detalle y límites: [Autorizaciones versionadas](RENOVACION_AUTORIZACIONES_IA.md).

La entrega `0046` pasa **148 pruebas backend y 142 subtests**, **14 Playwright** con API
simulada, **18 del instalador**, typecheck/build y SQL offline hasta `0046`. Registra 13
tareas duraderas y 186 rutas OpenAPI. Se recopilaron 120 tests de integración sin ejecutarlos.
Detalle: [Limpieza de importaciones](LIMPIEZA_IMPORTACIONES.md). No se han limpiado datos reales.

La entrega `0047` pasa **170 pruebas backend y 179 subtests**, **16 Playwright** con API
simulada, **18 del instalador**, typecheck/build y SQL offline hasta `0047`. Registra 14
tareas duraderas y 189 rutas OpenAPI. Se recopilaron 127 tests de integración sin ejecutarlos.
Detalle y límites: [Inventario de almacenamiento](INVENTARIO_ALMACENAMIENTO.md). No se ha
inventariado almacenamiento real ni activado una cuota global.

La entrega `0048` pasa **196 pruebas backend y 208 subtests**, **19 Playwright** con
API simulada, **18 del instalador**, typecheck/build y SQL offline hasta el único head
`0048`. Registra 15 tareas duraderas y 194 rutas OpenAPI. Se recopilaron 136 tests de
integración sin ejecutarlos. Las nuevas comprobaciones cubren admisión, reservas de
respuestas IA, recuento/cancelación, separación de controles, paginación de recibos,
copias acotadas y cuarentena. Detalle y límites: [Cuota de almacenamiento](CUOTA_ALMACENAMIENTO.md).
No se ha aplicado `0048`, activado cuotas ni ejecutado escrituras contra datos reales.

La entrega `0049` + `0050` pasa **213 pruebas backend y 241 subtests**, **22 Playwright**
con API simulada, **18 del instalador**, typecheck/build y SQL offline hasta el único head
`0050`. Mantiene 15 tareas duraderas y suma 197 rutas OpenAPI. Se recopilaron **142 tests
de integración sin ejecutarlos**. Incluye carreras de cancelación/inicio preparadas para
PostgreSQL y verificación explícita de los permisos heredados de auditoría. Detalle:
[Cancelación de reservas](CANCELACION_RESERVAS.md). No se han cancelado reservas reales.

El bloque PDF/A, sin nueva migración (head `0050`), añade conversión/verificación y
un worker opcional en Compose y release. Pasan **226 pruebas backend**, **25 Playwright
con API simulada**, **20 pruebas del instalador/Compose** y typecheck/build. SQL offline,
mappers y OpenAPI pasan con **199 rutas y 16 funciones duraderas**. Se recopilan
**145 pruebas de integración sin ejecutarlas**. Pasa además un ensayo sintético con
OCRmyPDF 17.11.0, veraPDF 1.30.2 y Ghostscript 10.02.1 reales instalados temporalmente:
3 páginas, perfil 2b válido, texto catalán cercable, facsímil y originales conservados,
rechazo de informe negativo. No sustituye la prueba de la imagen ni un libro real.
No se han desplegado servicios, publicado imágenes, consumido IA ni modificado datos.

El ensayo local de directorio generó 100.000 entradas JPG sintéticas en 100 libros
(enlaces duros a una imagen de prueba por libro): creación 2,794 s, escaneo y validación
de manifiestos 6,633 s. No mide transferencia HTTP, PostgreSQL/S3 ni diversidad paleográfica.

El Docker del entorno devuelve permiso denegado sobre su socket. **El flujo Compose
completo continúa sin ejecutarse**, pero se ha preparado un clúster nativo temporal
de PostgreSQL 16/pgvector, Redis y MinIO, con datos sintéticos, para ejecutar migraciones,
RLS y flujos de integración. Resultados, defectos y bloqueo de MinIO se registran en
[Estado de validación real](ESTADO_VALIDACION_REAL.md); los recuentos anteriores son
evidencia histórica, no una afirmación de que la integración siga sin probarse.

En un entorno Python con las dependencias ya declaradas:

```bash
python -m unittest discover -s backend/checks -v
```

Para las pruebas de integración, configurar exclusivamente servicios desechables y
una base de datos de pruebas antes de habilitar `GEN_SUITE_ALLOW_TEST_TRUNCATE=yes`.
Nunca reutilizar `config/.env` ni los volúmenes de la instalación personal.

Frontend:

```bash
npm run typecheck
npm run build
```
