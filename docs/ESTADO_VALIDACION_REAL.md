# Validación real: integración y almacenamiento pendiente de producción

Estado: **no apto todavía para producción**. Este informe sustituye únicamente las
afirmaciones históricas de «integración no ejecutada»; no da por terminado el plan.
Esta validación no despliega la aplicación ni modifica datos personales.

## Validación vigente: importación revisada del árbol (`0053`)

Se añade [importación revisada GEDCOM 7/GEDZIP](IMPORTACION_ARBOL.md): carga privada,
análisis previo, informe inmutable y append explícito en una transacción final.
Conserva originales, citas y varias digitalizaciones por fuente; no fusiona, publica
ni ejecuta IA. La migración `0053` solo se aplicó a la base **desechable**. No se han
añadido dependencias ni cambiado el método de instalación Compose.

- **265 pruebas unitarias correctas**, incluidos límites ZIP, rutas, referencias,
  nombres, fechas, notas compartidas y extensiones por URI.
- **33 pruebas de navegador correctas**, Chromium real con API simulada.
  Comprueban aceptación expresa, informe de otro original, reapertura y reintento.
- **40 pruebas de scripts correctas**; typecheck y build frontend correctos.
  Persiste el aviso previo de importación mixta de `linkage.ts`.
- Integración dentro de la imagen runtime: **172 correctas, 0 fallidas y 1 omitida**
  en 279,90 s. La omitida es la integración PDF/A, ejecutada separadamente en su
  imagen especializada; el build PDF/A pasa OCRmyPDF y veraPDF reales.
- En esa suite pasan los 8 casos de importación: HTTP, PostgreSQL/RLS y S3 reales,
  original conservado, revisión sin mutación, append idempotente, fuentes con varios
  documentos, cancelación tras subir, reintento explícito, corrupción, PDF sin IA,
  referencias de recuperación y los dos trabajos ejecutados por Redis/ARQ real.
- Head único `0053`, mapeadores y SQL de migración offline correctos: 2.465 líneas y
  207 rutas OpenAPI. La migración aplica RLS, referencias compuestas y protección de
  original/informe contra modificación.

La suite general precede a los últimos ajustes del índice de fechas y la consulta de
todos los documentos de una fuente. Tras esos ajustes se ejecutaron **28 casos
enfocados de importación, GEDCOM, investigación y RLS**, todos correctos en 39,30 s
(19 casos ajenos al filtro no seleccionados). Typecheck y build se repitieron con
éxito. No sumar tandas enfocadas a la suite general: se solapan.

Esto no certifica un importador universal ni una sandbox de archivos hostiles. Quedan
compatibilidad con consumidores externos, corpus adversarial, límites de proceso y
pruebas con archivos representativos/a máxima capacidad. El formato nativo por espacio
sigue pendiente. El recuperador v2 se ha ensayado de extremo a extremo con `0053`
entre dos clústeres PostgreSQL 16 y dos S3 independientes: **10 objetos, 6 referencias,
huellas de todas las tablas y 234 comprobaciones RLS**. Se han construido localmente
las imágenes runtime, PDF/A y frontend. La evidencia remota corresponde siempre al
SHA y a los enlaces de Actions publicados; no se ha hecho despliegue, migración
personal ni uso de IA de pago.

## Histórico: exportaciones del árbol (`0052`)

Se añaden GEDCOM 5.5.1/7.0 y GEDZIP en segundo plano, instantánea PostgreSQL de solo
lectura, comprobación de originales, resultados privados inmutables y revisión del
informe antes de descargar. La migración aditiva `0052` se aplicó **solo a la base
desechable**. No hay dependencias nuevas ni cambios al método de instalación Compose.
[Uso, semántica y límites](EXPORTACION_ARBOL.md).

- **250 pruebas unitarias correctas**, 6,59 s.
- **31 pruebas de navegador correctas**, 15,6 s, con Chromium real y API simulada.
- **40 pruebas de scripts correctas**, 1,38 s.
- Typecheck y build frontend correctos; persiste el aviso previo de importación mixta
  estática/dinámica de `linkage.ts`.
- Integración general: **159 correctas, 2 fallidas, 0 omitidas**, 226,99 s. Ambas fallas
  siguen siendo las variantes multipart del MinIO antiguo, sin `xfail` ni relajación.
- Tras los ajustes finales se ejecutaron **16 casos de árbol/exportación/RLS**, todos
  correctos en 22,13 s. Incluyen PostgreSQL y S3 reales, la cola transaccional con
  Redis/ARQ real, árbol editado durante la instantánea, cancelación después de subir,
  originales PNG y PDF (este último aún sin rasterizar), referencias de recuperación,
  límite de capacidad y aislamiento. No sumar estos 16 a la suite general: se solapan.

La suite general se ejecutó antes de los últimos ajustes menores y de añadir los
casos ARQ/PDF; la tanda final cubre esos cambios. La migración aplica RLS, FK compuesta,
prefijo propio y revoca UPDATE/DELETE heredados por el rol de aplicación.

Se hizo además una comprobación inicial con el validador independiente `js-gedcom`,
sin añadirlo al producto. Tres grupos sintéticos no dieron errores y dos señalaron
discrepancias con fechas vacías/PHRASE y espacios iniciales que la norma permite.
Se conservan los resultados, sin presentarlos como certificación. Faltan más consumidores,
el formato nativo por espacio y pruebas de capacidad. En aquella entrega tampoco
existía aún la importación revisada añadida posteriormente en 0053.

El recuperador incluye `tree_exports` y admite `0052`; se ha probado la referencia
con PostgreSQL real. El ensayo completo de restauración anterior seguía en `0051`:
el nuevo fixture completo incorpora GEDZIP, pero todavía no se ha vuelto a ejecutar
entre dos clústeres con `0052`. Tampoco se han construido imágenes Docker, ejecutado
Compose ni CI remoto, migrado datos personales o utilizado proveedores de IA de pago.

## Histórico: reparación del intercambio GEDCOM 5.5.1

Se han corregido pérdidas bibliográficas y de citas en GEDCOM 5.5.1, añadido importación
de fuentes/repositorios y revisión previa de pérdidas con huellas del archivo/informe.
Pasan **242 unitarias**, **28 pruebas de navegador con API simulada**, **40 de scripts** y
typecheck/build. La integración general terminó con **150 correctas y 2 fallidas**, sin
omisiones, en 230,34 s; ambas fallas siguen siendo las variantes multipart MinIO.
Después de los ajustes finales se repitieron los **9 casos de árbol/fuentes**, todos correctos.
En aquella entrega no hubo dependencias, migraciones ni cambios de datos personales nuevos. GEDCOM 7/GEDZIP,
exportación nativa completa y compatibilidad con consumidores independientes siguen
pendientes. [Cobertura y límites](INTERCAMBIO_GEDCOM.md).

## Entorno del ensayo

Se han ejecutado servicios reales en un directorio temporal privado, escuchando solo
en loopback, con credenciales y datos sintéticos independientes de la instalación:

- PostgreSQL 16.15, pgvector 0.6.0 y roles propietario/aplicación separados.
- Redis 7.0.15 y worker ARQ real para el recorrido PDF/A.
- Binario oficial MinIO `RELEASE.2025-04-22T22-12-26Z`, la versión declarada en Compose.
- OCRmyPDF 17.11.0, veraPDF 1.30.2 y Ghostscript 10.02.1 para los derivados sintéticos.

Los binarios se extrajeron en carpetas temporales; no se instalaron servicios del
sistema ni dependencias nuevas del proyecto. No se leyó `config/.env`, no se reutilizó
`./data` y no se llamó a proveedores de IA. Antes de cada ejecución destructiva de
pytest se verificó `SHOW data_directory` y el nombre de la base desechable. El fixture
exige además `GEN_SUITE_ALLOW_TEST_TRUNCATE=yes` y `gensuite_test_*`/`gensuite_ci`.

El acceso al daemon se realizó mediante `sudo` sin tocar la instalación personal.
Las tres imágenes se construyen, y las combinaciones base/release/importación/recuperación
de Compose se validan sintácticamente. No se ha arrancado el stack público completo.
El MinIO antiguo se usó exclusivamente como sistema bajo prueba, no como recomendación
de seguridad.

## Defectos corregidos a partir de las pruebas

1. La publicación de una importación reanudable escribía `resumable_upload`, valor
   rechazado por el CHECK de `documents.source_kind`. La migración nueva `0051`
   añade ese valor sin reinterpretar los anteriores ni reescribir documentos.
2. La auditoría de una conexión Gramps insertaba `interop_connection` en un varchar(16).
   `0051` amplía `change_log.entity_type` a 64 caracteres y actualiza el modelo.
   El downgrade rechaza datos incompatibles; no trunca nombres ni elimina fuentes.
3. La instantánea Gramps referenciaba una variable `connection` no definida. Ahora
   carga la conexión bajo RLS antes de asignar identidades estables a objetos locales.
4. Los listados de versiones rechazaban el marcador de versión explícitamente vacío
   de un bucket sin versionado. Inventario y limpieza conservan el cursor del servidor,
   distinguiendo `""`, ausencia y el identificador de objeto literal `"null"`. Un cursor
   opaco nunca se utiliza como clave de borrado; los fragmentos conservan su validación
   estricta de espacio/sesión/formato. Se añade regresión de más de 100 fragmentos.
5. Una prueba RLS chocaba primero con la unicidad de posición de etapa, ocultando la
   comprobación del padre de otro espacio. Usa otra posición y sigue exigiendo el
   error de clave foránea `23503`; no se relajó el aislamiento ni la aserción.

El recuperador ya admitía explícitamente `0051`, manteniendo invalidación de inventarios,
cuarentena de reservas y auditoría anterior. No se editaron migraciones existentes.

## Qué se ha probado

La primera suite real se detuvo con 89 correctas y 10 fallidas. Tras las correcciones,
una ejecución completa pasó **144 pruebas y falló 2**, ambas de inventario multipart
en MinIO; no se omitieron ni transformaron en éxitos esperados. Se añadió después
otra regresión de paginación de staging, incluida en la verificación final.

Resultado de la tanda `0051` previa al ensayo completo de recuperación:

- **Integración: 145 correctas, 2 fallidas, 0 omitidas**, 203,38 s, con el test PDF/A
  real habilitado. Las fallidas siguen siendo exclusivamente las dos variantes multipart.
- **228 pruebas unitarias y 271 subtests correctos**, 8,09 s.
- **20 pruebas del instalador correctas**, 1,18 s; YAML de CI analizado sin error.
- `npm run typecheck` y `npm run build` correctos. Se mantiene el aviso previo de
  importación estática/dinámica de linkage, sin impedir la compilación.
- Un único head `0051`, 2.368 líneas de SQL offline, mappers y 199 rutas OpenAPI
  válidos; `git diff --check` sin errores. `0051` aplicada realmente solo al clúster temporal.

Los servicios de ensayo se detienen al finalizar; las carpetas temporales se conservan
para revisión. No se borraron archivos del proyecto ni se creó un commit o una release.

El ensayo PDF/A añadido, `backend/tests/test_pdfa_runtime.py`, cubre realmente:

- Carga autenticada de dos imágenes y una transcripción corregida sintética.
- Heartbeat del worker dedicado, solicitud duplicada y outbox confirmado en PostgreSQL.
- Envío a Redis/ARQ, ejecución real de OCRmyPDF y validación independiente de veraPDF.
- Cuatro páginas conservadas en orden, texto catalán cercable y ausencia de OCR nuevo.
- Persistencia S3, descarga autenticada y Range, checksums del PDF y del informe XML.
- Corrección posterior que marca el derivado como obsoleto sin sobrescribirlo.
- Imágenes originales recuperadas byte por byte sin cambios.

La recuperación probada incluye catálogo, referencias, snapshot PostgreSQL exportado,
`pg_dump` real y comprobaciones RLS. Para los clientes extraídos fuera de `/usr/bin`
se utilizó un adaptador **solo del ensayo** que resuelve el ejecutable y su biblioteca;
no falsea resultados ni modifica el entorno restringido del recuperador de producción.
Ese caso limitado se complementa ahora con el ensayo completo siguiente.

### Cierre del recorrido de recuperación sintética

`checks/smoke_recovery.py` pasa desde un origen nuevo hasta otro clúster PG16 y otro
S3 vacíos: **8 objetos, 4 referencias y 213 comprobaciones RLS**. Comprueba dos espacios,
seis personas, relaciones familiares, fuentes/citaciones, originales, revisiones,
vectores históricos y PDFs. Los hashes de 11 tablas de evidencia coinciden; las
credenciales ficticias siguen siendo descifrables y el origen queda intacto.
El destino permanece en cuarentena y `NOLOGIN`. Se rechazan una conexión concurrente
en el origen, la clave maestra incorrecta y la reutilización del destino poblado.

El ensayo encontró y permitió corregir dos bloqueos reales adicionales del recuperador:
campos internos del catálogo devueltos como bytes no serializables y casts de arrays
que PostgreSQL reescribe al reconstruir restricciones/índices. La comparación acepta
solo esa equivalencia acotada; sigue rechazando diferencias en controles o evidencia.
La suite unitaria pasa **233 tests/293 subtests**, la regresión de snapshot PostgreSQL
pasa y typecheck/build/20 pruebas del instalador pasan. La suite de integración completa
repetida después de estos cambios mantiene **145 correctas y 2 fallidas**, sin omisiones,
en 217,57 s; son las mismas dos variantes multipart MinIO. No hay nueva migración.

El workflow `verify-recovery` añade el ensayo repetible con dos PostgreSQL/S3 y conserva
sus artefactos sintéticos. En aquella etapa aún no se había ejecutado en Docker; la
validación vigente de `0053` descrita al principio ya lo ejecuta. Falta probar
fallos más amplios, escala, transferencia externa real y custodia/retención operativas.
Detalles y reproducción: [Recuperación completa](RECUPERACION_COMPLETA.md).

El nuevo test PDF/A es opt-in (`GEN_SUITE_RUN_PDFA_INTEGRATION=yes`) y falla si faltan
las herramientas cuando está habilitado. El workflow CI lo incluye en la imagen
opcional, separado de la prueba sintética sin red. La evidencia publicada debe
consultarse en Actions para el SHA exacto.

## MinIO: multipart corregido; bloqueo de seguridad pendiente

Con la versión incluida, `ListMultipartUploads(Prefix="<tenant>/")` devolvió una
lista vacía aunque `CreateMultipartUpload`, `UploadPart` y `ListParts` confirmaban
una parte de 10 bytes. Resultado observado:

| Bucket | Bytes esperados | Bytes observados por la app |
| --- | ---: | ---: |
| Sin versionado | 28 | 18 |
| Con versionado | 37 | 27 |

No era un fallo de expectativa del test. El inventario conserva el listado prefijado
como ruta principal. Si ese endpoint devuelve vacío, hace una sola sonda global acotada
a menos de 100 cargas, descarta toda clave que no tenga el UUID exacto del tenant y
revalida cada candidata con `ListParts` en el bucket solicitado. Una carga filtrada de
otro bucket devuelve `NoSuchUpload` y no se persiste. Una página llena, truncada,
duplicada o con cabeceras/cursor ambiguos falla cerrada. Las dos variantes reales pasan
y vuelven a contar los 10 bytes multipart sin convertir datos ajenos en filas propias.

Una prueba diagnóstica sin prefijo devolvió además cargas de otros buckets de pruebas.
Por eso esa compatibilidad es estrictamente acotada y valida bucket/carga antes de
guardar: no acepta el resultado global como inventario ni conserva ninguna clave
ajena. El código oficial
de esa versión documenta que el listado multipart no admite prefijos generales:
[implementación MinIO](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/cmd/erasure-multipart.go),
[incidencia del proyecto](https://github.com/minio/minio/issues/13246).
La compatibilidad necesaria es la de [ListMultipartUploads de S3](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListMultipartUploads.html),
no solo que un endpoint acepte la petición y devuelva HTTP 200.

Hay además un bloqueo de seguridad independiente: el aviso oficial
[GHSA-hv4r-mvr4-25vw](https://github.com/minio/minio/security/advisories/GHSA-hv4r-mvr4-25vw)
afecta a la edición comunitaria incluida y describe escrituras sin verificar la
firma cuando se conoce una access key válida. La corrección publicada corresponde
a **AIStor**, no a una nueva imagen comunitaria equivalente. El repositorio original
está archivado. No se contrató AIStor ni se sustituyó el servicio sin autorización.

La descarga antigua directa devolvió 410 y la consulta anónima de la etiqueta en
Docker Hub no dio acceso. El mismo binario oficial sí pudo obtenerse desde Quay.
CI usa Quay para reproducir el backend antiguo **solo en pruebas**; cambiar de registro
no corrige los problemas anteriores. Los Compose de la instalación personal no se
han cambiado para apuntar a otro producto ni se han migrado sus objetos.

## Decisión y siguientes gates

Sigue siendo necesario evaluar e incorporar un backend S3 mantenido o un proveedor
externo antes de declarar aptitud de producción. La alternativa debe acreditar versiones,
multipart por bucket/prefijo, paginación, metadatos, Range, copias, permisos,
recuperación y mantenimiento de seguridad. Esto no autoriza migrar ni borrar datos
existentes, contratar servicios o enviar documentos fuera.

`docker-release.yml` ya incorpora un [gate automático](GATE_PUBLICACION.md) para release
y ejecución manual: exige CI y recuperación de un push de main del SHA exacto, con todos
sus jobs correctos. Bloquea resultados antiguos que oculten la última ejecución fallida
o pendiente, así como evidencia ausente/incompleta o errores de API. El build descarga
el SHA verificado y los permisos de publicación solo están en el job dependiente.
Pasan **40 pruebas de scripts**, las **265 unitarias backend** y typecheck; los tres
workflows tienen YAML válido. La protección remota de `main` sigue siendo una decisión
de administración del repositorio. Los enlaces de Actions, no este texto, acreditan
si el gate remoto pasó para un SHA concreto.

Antes de producción siguen pendientes, entre otros:

- Resolver el bloqueo de seguridad del backend S3; la integración multipart ya está verde.
- Ejecutar el stack Compose aislado completo y probar una actualización/reversión.
- Importación HTTP/S3 completa de 100.000 originales y medición de capacidad física.
- Manuscritos reales con verdad de referencia, métricas por campo e inferencias revisables.
- Ampliar el ensayo de backup/restauración a fallos/escala y completar retención,
  custodia real de claves y backfill histórico autorizado.
- Exportación nativa completa por espacio y más compatibilidad externa GEDCOM/GEDZIP.

El [plan integral](IMPLEMENTACION_INVESTIGACION.md) permanece abierto.
