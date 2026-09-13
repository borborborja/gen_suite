# Exportaciones privadas en segundo plano

[Aviso de la especificación GEDCOM](NOTICE_GEDCOM.txt).

En **Mi árbol → Exportaciones en segundo plano y GEDZIP** se pueden generar GEDCOM
5.5.1, GEDCOM 7.0 o un paquete `.gdz` con documentos. El panel permite consultar el
progreso, cancelar, volver después y revisar el informe del archivo ya generado antes
de descargarlo. No sustituye la [copia completa de recuperación](RECUPERACION_COMPLETA.md).

## Alcance e integridad

- La lectura del árbol, bibliografía, citas y metadatos usa una transacción PostgreSQL
  `REPEATABLE READ`, de solo lectura y con RLS. Termina antes de transferir documentos.
  Las ediciones posteriores no cambian ni el archivo ni su informe.
- GEDZIP incluye los PDF originales y las páginas originales de los documentos
  **propios y retenidos**, incluidos los todavía no citados. No exporta automáticamente
  documentos públicos de otro espacio, documentos de solo datos, medios personales,
  manifiestos históricos de ingesta, derivados PDF/A, revisiones, inferencias, proyectos,
  permisos, credenciales ni historial operacional.
- Los originales deben disponer de un checksum previo. Cada respuesta S3 se lee en
  fragmentos de hasta 1 MiB y se comprueban longitud y SHA-256 contra la instantánea.
  Ausencias, purgas parciales, tamaños inesperados o checksums distintos impiden publicar
  el paquete entero; no se anuncia éxito parcial. No se altera ni borra el original.
- Cada archivo multimedia tiene una entrada `FILE` dentro de `OBJE`, con tipo MIME.
  Los nombres del ZIP se generan con UUID y posiciones, nunca con rutas del usuario.
  `gedcom.ged` enlaza estos archivos y fuentes. Los documentos sin fuente enlazada se
  incluyen como entradas bibliográficas, sin inventar citas para ninguna persona.
- `gen-suite/media-manifest.json` conserva nombres de origen, orden de páginas, tamaños
  y checksums. `gen-suite/interchange-report.json` describe el GEDCOM incluido; no puede
  contener el checksum del ZIP que lo contiene. El informe descargable desde la API
  añade ese checksum del paquete completo y la huella de revisión ligada al archivo.

La conservación de bytes no prueba la verdad genealógica ni la exactitud de una lectura.
La exportación conserva información privada y de personas vivas: **no está anonimizada**.

## Trabajo persistente y acceso

La petición crea `Job` y `TaskDispatch` en la misma transacción. `export_tree` está
registrada en el worker habitual; no necesita otro servicio ni una dependencia nueva.
Se admite una exportación activa por espacio. Repetir la petición durante su ejecución
reutiliza la misma tarea si el formato coincide; otro formato devuelve 409.

El archivo solo se publica al completar la subida y confirmar, bajo bloqueo del job,
que no se canceló. La fila `tree_exports` y el estado completado se confirman juntos.
La migración **0052**, aditiva, aplica RLS, referencia compuesta al job del mismo espacio,
prefijo privado y permisos sin UPDATE/DELETE para el rol de aplicación. No modifica
personas ni bibliografía existentes. La descarga sigue pasando por el backend.

La cancelación se comprueba entre fases y durante las descargas de originales. No
interrumpe necesariamente un PUT/multipart ya iniciado. Si la cancelación o un fallo
llegan después de una escritura, el objeto puede quedar huérfano: el recibo y sus bytes
siguen contabilizados, sin borrados ni liberaciones de cuota supuestos. Reintentar una
tarea fallida genera una nueva exportación/instantánea, no reanuda por páginas. Un
reintento del worker usa otra clave S3 y nunca sobrescribe un resultado publicado.

API:

- `POST /api/tree/exports`, `{ "kind": "gedcom551" | "gedcom7" | "gedzip" }`: 202;
  requiere investigador o administrador del espacio.
- `GET /api/tree/exports`: últimas 100 tareas y resultados del espacio.
- `GET /api/tree/exports/{id}/audit`: informe del resultado ya publicado.
- `GET /api/tree/exports/{id}/content?review_token=…`: archivo privado; la huella debe
  corresponder al informe. Admite Range e If-Range. Nunca entrega una URL S3.
- `POST /api/jobs/{id}/cancel`: cancelación cooperativa existente.

Los navegadores con guardado directo escriben la descarga a disco. Sin ese soporte,
se rechazan archivos mayores de 200 MiB antes de descargarlos; también se comprueba
la longitud recibida. La interfaz no verifica incrementalmente SHA-256 en el cliente.

## Perfil GEDCOM 7 y extensiones

Se genera UTF-8, sin `CHAR` ni `CONC`, con el escape de arroba inicial y continuaciones
`CONT` de la [especificación 7](https://gedcom.io/specifications/FamilySearchGEDCOMv7.html).
Las citas sin fuente identificada usan `@VOID@`, no una fuente ficticia. Los tipos de
nombre y filiación se convierten a enumeraciones; un tipo de nombre desconocido usa
`OTHER` y su texto en `PHRASE`.

Las fechas reconocidas se conservan en la gramática estándar (incluidos intervalos,
aproximaciones y fechas gregorianas/julianas con días comprobados). Calendarios no
interpretados, fechas dobles y frases ambiguas se conservan íntegramente en `DATE.PHRASE`,
sin inventar una fecha normalizada. El informe cuenta estas conversiones.

`HEAD.SCHMA.TAG` documenta estos identificadores propios, no registrados en FamilySearch:

| Etiqueta | URI estable | Payload JSON, versión 1 |
| --- | --- | --- |
| `_GS_SOURCE` | `urn:uuid:564f39fd-1d83-57f5-90d8-c08f1a601dcf` | Tipo, URL, signatura y metadatos originales de una fuente. |
| `_GS_CITATION` | `urn:uuid:bc2aef8c-5f35-506e-93f5-8829817ac16c` | Localizador estructurado, calidad, ausencia de fuente y tipo de destino de la cita. |
| `_GS_LEGACY` | `urn:uuid:16e5370f-cb6d-5fd4-bf42-4bb5b3b6b4bd` | `structure`: árbol original no interpretado; sus referencias son texto histórico, no enlaces activos. |
| `_GS_EMPTY` | `urn:uuid:89b39d97-69a5-55a9-9984-2d71d04ddceb` | `empty: true`: estructura modelada sin valor ni datos adicionales; no afirma que un evento ocurrió. |

El receptor puede ignorar extensiones. No se anuncia una conversión universal sin
pérdidas, certificación oficial ni compatibilidad completa con todos los consumidores.
La [importación revisada GEDCOM 7/GEDZIP](IMPORTACION_ARBOL.md) añade un perfil inicial
con originales privados y append explícito. **El paquete nativo portable por espacio
sigue pendiente**; exportar y reimportar GEDZIP no restaura toda la investigación.

## Capacidad y límites operativos

Compose sigue siendo el método de instalación. Aplicar la migración y reconstruir
API/worker/frontend mediante el procedimiento habitual; no se ha desplegado sobre
datos personales durante el desarrollo. Variables opcionales en `config/.env.example`:

| Variable | Por defecto |
| --- | ---: |
| `TREE_EXPORT_MAX_ROWS` | 100 000 filas consideradas para la instantánea |
| `TREE_EXPORT_MAX_GRAPH_BYTES` | 32 MiB de datos textuales del grafo |
| `TREE_EXPORT_MAX_FILES` | 10 000 originales |
| `TREE_EXPORT_MAX_BYTES` | 2 GiB del resultado |

Estos límites son salvaguardas, **no resultados de una prueba de capacidad**. El grafo
todavía se materializa en memoria. El ZIP se construye en disco temporal y se almacena
sin compresión; hay que dimensionar espacio, concurrencia entre espacios y cuota física
S3. No hay borrado/retención automática de exportaciones ni descarga reanudable gestionada
por la interfaz (Range queda disponible en la API). El problema multipart del MinIO
antiguo y su bloqueo de producción no quedan resueltos por esta función.

El recuperador incorpora estos archivos como referencias privadas en 0052 y mantiene
la lectura de esquemas anteriores. El ensayo completo de restauración anterior era
0051; no presentar su éxito como una restauración ya ejecutada del esquema 0052.

## Validación independiente inicial

Se ejecutó localmente `gedcom7code/js-gedcom`, commit
`9296068bd6a461cdd93f9edc74d8457995897afe`, con el registro oficial disponible durante
el ensayo. Solo recibió ejemplos sintéticos por stdin; no se enviaron árboles a sitios
externos ni se añadió esta herramienta a las dependencias de la aplicación.

Tres grupos (registro mínimo, bibliografía/medios y eventos con fecha estándar) no
produjeron errores; hubo avisos de extensiones propias y tipos no entendidos por el
validador. Dos grupos produjeron errores: `DATE` sin payload con `PHRASE`, y `CONT`
con un espacio inicial significativo. La norma admite expresamente una fecha vacía
con subestructura (§2.4) y que el texto empiece por espacio (§1.3). La inspección del
validador muestra que interpreta el valor ausente como `undefined` y consume espacios
del payload como delimitadores. No se modificó la herramienta ni se ocultaron estos
resultados. Hace falta contrastar los casos con más consumidores independientes.

Las pruebas del repositorio están en `test_gedcom7.py`, `test_tree_exports.py`, el caso
RLS `tree_exports` y `gedcom-ui.spec.ts`. Los recuentos vigentes se publican en el
[estado de validación](ESTADO_VALIDACION_REAL.md).
