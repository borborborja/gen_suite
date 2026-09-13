# Importación revisada de GEDCOM 7 y GEDZIP

Disponible en **Mi árbol → Importación revisada GEDCOM 7 y GEDZIP**, con esquema
aditivo **0053**. No necesita dependencias nuevas ni cambia la instalación mediante
Compose. No convierte la suite en un producto ya apto para producción: véase el
[estado de validación](ESTADO_VALIDACION_REAL.md).

## Flujo y garantías

1. Seleccionar un `.ged` 7.0 o un `.gdz`. La carga conserva el original privado con
   tamaño y SHA-256; todavía no incorpora personas, familias ni documentos a la biblioteca.
   No cerrar el panel hasta terminar la carga. Los trabajos posteriores sobreviven al cierre.
2. El worker comprueba la sintaxis y referencias, inspecciona los medios locales del
   paquete y guarda un informe inmutable. Se puede descargar el original incluso si
   el análisis falla. El informe enumera recuentos, transformaciones y limitaciones.
3. Revisar y aceptar expresamente **añadir registros privados nuevos**, sin fusionar,
   sustituir ni eliminar los existentes. La confirmación queda ligada a la huella del informe.
4. Otro trabajo vuelve a comprobar el original y el análisis. Prepara los originales
   de los documentos y, en una sola transacción final, incorpora árbol, fuentes, citas,
   documentos y sus vínculos. Las personas entran con privacidad `private` y los
   documentos con retención de imágenes y `may_contain_living=true`.

Las citas se asignan a persona, nombre, evento o familia según su destino. Los
enlaces `SOUR.OBJE` vinculan una fuente a **varios documentos**, sin fabricar citas a
personas. Las imágenes del mismo `OBJE` conservan el orden como páginas de un libro;
PDF y grupos mixtos generan documentos separados. PDF mantiene sus bytes originales
y solo programa rasterización: **no dispara transcripción, embeddings ni gasto de IA**.
Los vínculos múltiples también se utilizan al exportar de nuevo GEDZIP.
La consulta de fuentes devuelve todos sus documentos vinculados y el panel de
investigación permite abrirlos.

Cancelar antes de la transacción final no deja un árbol parcial. La cancelación y
el commit compiten por el bloqueo del trabajo: si el commit ya ganó, el resultado
queda completado, no se promete deshacerlo. Un reintento tras error/cancelación exige
confirmación independiente. Repetir la confirmación de un trabajo activo o completado
devuelve el mismo trabajo; no vuelve a añadir el árbol.

Los intentos S3 usan claves nuevas. Si se interrumpe después de subir, pueden quedar
objetos huérfanos **contabilizados**; no se borran ni se devuelve cuota suponiendo que
no llegaron al servidor. No existe aún papelera/retención completa de importaciones.

## Perfil compatible y pérdidas explícitas

- GEDCOM **7.0**, UTF-8 estricto, referencias locales coherentes, continuaciones `CONT`
  y escape de arroba inicial propios de 7. No se reutiliza el parser 5.x ni se cambia
  simplemente la versión de cabecera. La ruta clásica sigue limitada a 5.5/5.5.1.
- Esto **no es un validador completo de toda la norma**, ni garantiza compatibilidad
  con todas sus extensiones o programas. Rechaza ambigüedades de campos esenciales.
- Notas compartidas se copian como texto, sin identidad compartida editable. Fechas
  PHRASE pueden conservarse como fecha textual; calendarios juliano, hebreo,
  republicano francés y BCE no generan un año gregoriano supuesto. Otros calendarios
  y gramáticas de fecha requieren más cobertura antes de afirmar interpretación completa.
- Nombres se proyectan a los campos disponibles: sufijos, metadatos y subestructuras
  pueden quedar solo en el original. Hay límites de longitud y avisos de truncamiento.
  Identificadores largos se remapean internamente; nunca se interpretan como UUID locales.
- Solo se interpretan extensiones propias por su **URI declarada**, no por coincidir
  su etiqueta. Etiquetas `_GS_*` ajenas no sobrescriben nuestros metadatos.
- Multimedia directamente asociada a personas, familias, eventos o citas, relaciones
  no recíprocas, repositorios adicionales y otros datos no representados conservan
  su evidencia en el original, pero no tienen necesariamente un editor o enlace activo.
  Las representaciones alternativas `TRAN` se comprueban, pero permanecen solo en el paquete.
- GEDCOM sin ZIP no adjunta rutas locales. Ninguna variante descarga URLs externas,
  rutas `file://` ni recursos de red; esos enlaces permanecen en el original.
- No restaura UUID, revisiones, actas, inferencias, cuentas, permisos o credenciales de
  otra instalación. **No sustituye la copia completa ni el futuro formato nativo por espacio.**

El informe limita la lista detallada a 5.000 transformaciones y conserva el total
por código. No es un inventario exhaustivo de todas las semánticas GEDCOM ni acredita
la veracidad de una relación o fuente. Conservar también una copia independiente del
archivo original; no usar la suite como único ejemplar de una colección.

## Límites y seguridad

Valores predeterminados:

| Límite | Valor |
| --- | --- |
| Carga total (`TREE_IMPORT_MAX_BYTES`) | 2 GiB |
| Expansión ZIP y medios referenciados, incluyendo repeticiones | 4 GiB |
| Medios referenciados (`TREE_IMPORT_MAX_FILES`) | 10.000 |
| GEDCOM y proyección aproximada en memoria | 32 MiB |
| Estructuras GEDCOM, incluidas continuaciones | 100.000 |
| Profundidad / longitud de línea | 64 / 1 MiB |
| Medio individual analizado | 256 MiB |
| Imagen individual | 40 millones de píxeles, una página |

El límite de grafo y estructuras reutiliza `TREE_EXPORT_MAX_GRAPH_BYTES` y
`TREE_EXPORT_MAX_ROWS`. Ajustarlos afecta también a exportación. Aumentar carga
por encima de 2 GiB requiere ajustar el proxy; su ubicación exacta `/api/tree/imports`
transmite sin almacenar primero todo el cuerpo en Nginx. El backend autentica antes
de consumir el cuerpo y aplica el límite aunque no haya `Content-Length`.

GEDZIP admite Stored/Deflate, nombres UTF-8 y JPG/PNG/WebP/TIFF de una página/PDF.
Rechaza cifrado, volúmenes separados, enlaces simbólicos, nombres duplicados,
traversal, rutas ambiguas, tipos incompatibles y expansión excesiva. No utiliza
`extractall`, nombres del usuario como destinos de disco ni solicitudes externas.
Verifica CRC ZIP, tamaño y SHA-256. Las referencias repetidas cuentan para evitar
amplificación. Un fallo invalida el análisis completo, no publica un paquete parcial.

El análisis de medios usa bibliotecas locales dentro del worker: **no es una sandbox
para archivos hostiles**. Faltan corpus adversariales más amplios, límites de proceso
aislado, pruebas a capacidad máxima y consumidores externos. No habilitar cargas
públicas anónimas basándose solo en estas comprobaciones.

## API, operación y recuperación

- `POST /api/tree/imports?client_id=UUID&filename=...&kind=gedcom7|gedzip`: cuerpo binario,
  identificador de envío estable para reintentar una respuesta de carga incierta.
- `GET /api/tree/imports`, `GET /{id}/audit`, `GET /{id}/original`: consulta propia;
  el original permite Range y no expone claves S3. La descarga grande requiere navegador
  con guardado a disco; el fallback en memoria está limitado a 200 MiB.
- `POST /{id}/apply`: `review_token`, `acknowledge_append: true` y, si procede,
  `retry_failed: true`. Escritura reservada a administrador del espacio/investigador.
- Cancelación mediante la API de trabajos. El análisis y la aplicación utilizan el
  outbox transaccional y funciones ARQ registradas; ninguna petición depende de que
  Redis esté disponible al confirmar su transacción.

El original y el informe son inmutables en PostgreSQL; RLS y referencias compuestas
impiden enlazar originales, trabajos, fuentes o documentos de otro espacio.
Una versión nueva del analizador puede cambiar el informe: la aplicación rechaza
entonces la revisión vieja. Cargar un nuevo envío permite obtener otra revisión.
Una preparación fallida también requiere un envío nuevo; no hay reanálisis in situ.

El recuperador de instalación admite **0053**, incluye los originales de `tree_imports`
y conserva `source_documents`. Los trabajos pendientes se restauran pausados, sin
reactivar importación ni rasterización automáticamente. La referencia S3 y RLS están
probadas con servicios reales; el último ensayo completo entre dos clústeres sigue
correspondiendo a **0051**, no anunciar restauración completa 0053 ya acreditada.

Contrato consultado: [GEDCOM 7, sintaxis y GEDZIP](https://gedcom.io/specifications/FamilySearchGEDCOMv7.html).
Pruebas: `backend/checks/test_gedcom7_import.py`, `backend/tests/test_tree_imports.py`,
`backend/tests/test_rls_isolation.py` y `frontend/e2e/gedcom-ui.spec.ts`.
