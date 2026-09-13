# Intercambio GEDCOM y auditoría de pérdidas

Este documento describe la reparación de **GEDCOM 5.5/5.5.1** y su revisión previa.
La entrega posterior añade [exportaciones en segundo plano y GEDZIP con documentos](EXPORTACION_ARBOL.md).
Existe además [importación revisada GEDCOM 7/GEDZIP](IMPORTACION_ARBOL.md) con un perfil
limitado. La exportación/restauración nativa portable por espacio sigue pendiente.
Un `.ged` **no es una copia de seguridad** de la colección documental.

## Cambios en el producto

En **Mi árbol → Exportar GEDCOM**, primero se presenta un informe: personas, familias,
fuentes, repositorios y citas exportadas, citas sin destino representable, referencias
antiguas sin resolver y recuentos de los registros documentales/de investigación excluidos.
Permite guardar el informe JSON y cancelar sin descargar el árbol. Advierte que la
descarga incluye personas privadas o vivas y no es una publicación anonimizada.

La descarga solicita el SHA-256 del archivo revisado y la huella del informe completo.
Si cambian los bytes o los recuentos/advertencias, devuelve 409 y exige otra revisión.
Así, añadir un proyecto que queda fuera del archivo invalida el informe aunque no
cambie el GEDCOM. El informe y el archivo llevan `Cache-Control: private, no-store`.

| Información | Representación actual |
| --- | --- |
| Fuentes bibliográficas, incluidas las todavía no citadas | Registros `SOUR`: título, autor, publicación, signatura y referencia al repositorio. |
| Repositorios del espacio | Registros `REPO`: nombre, dirección, URL y notas. |
| Citas de personas, nombres, eventos y familias | `SOUR` bajo su destino exacto; las de un nombre ya no se atribuyen a toda la persona. |
| Folio/localizador, transcripción citada y nota | `PAGE`, `DATA.TEXT`/`TEXT` y `NOTE`, con continuación sin truncar texto largo. |
| Localizador estructurado y calidad documental | Extensión `_GS_CITATION`; no se convierte “original” en una puntuación `QUAY`. |
| Tipo, URL y metadatos originales de la fuente | Extensión `_GS_SOURCE`, además de la información bibliográfica legible. |
| Cita sin fuente identificada | Cita descriptiva explícita; al reimportar no inventa una fuente bibliográfica. |
| Notas personales, tipo de nombre y filiación estándar | Notas, `NAME.TYPE` y `FAMC.PEDI`. |

Las extensiones son privadas: la suite las conserva al reimportar, pero otros programas
pueden ignorarlas. Las signaturas se emiten bajo `SOUR.REPO.CALN`, incluso con repositorio
no identificado, no como `SOUR.CALN`. La distinción entre la cita con puntero y la descripción
libre sigue la [especificación GEDCOM 5.5.1](https://gedcom.io/specifications/ged551.pdf).

El importador ahora materializa fuentes, repositorios y citas. Antes conservaba algunos
punteros en `raw` pero no sus fuentes, y perdía citas dentro de nombres/eventos. Los
identificadores duplicados o referencias bibliográficas sin destino se rechazan antes de
insertar datos. Los UUID de destino se crean en el espacio de importación, no se toman
de identificadores de otra instalación.

El parser conserva espacios significativos, saltos de línea y arrobas literales. El
serializador divide valores largos con `CONC`/`CONT`, manteniendo el límite de 255
caracteres por línea de 5.5.1. Una nota literal como `@N1@` no se convierte en un enlace.
Si otro programa edita el texto o la nota de una cita, esos campos estándar prevalecen
sobre extensiones antiguas. Un `PAGE` modificado se conserva junto al localizador
estructurado anterior para su revisión, sin inventar una resolución del conflicto.

## Filiación y archivos antiguos

La cita histórica `family_child` almacena el ID del hijo, no el de una relación familiar
concreta. Se exporta con el hijo y una aclaración, sin elegir una de sus familias ni
atribuir evidencia a todos sus miembros. El informe cuenta esta reasignación y la extensión
conserva el tipo original para la reimportación. No soluciona la ambigüedad del modelo.

Si una estructura antigua tiene un puntero que no se puede resolver de manera unívoca y
con el tipo adecuado, se conserva como nota JSON identificada como referencia sin resolver.
Nunca se vincula por coincidencia de un antiguo `@S1@` a una fuente nueva ni a una persona.
Las importaciones antiguas **no se han reescrito ni reparado mediante backfill**. Conservar
el GEDCOM original; recuperar sus fuentes en datos existentes exige un procedimiento aparte.

## API

- `POST /api/tree/import/gedcom`: importación; devuelve también cantidades de fuentes,
  repositorios y citas materializadas. Mantiene permisos de administración/investigación.
- `GET /api/tree/export/gedcom/audit`: informe JSON con `sha256`, `review_token`, recuentos
  y advertencias. Es una lectura bajo RLS, no inicia trabajos ni escribe objetos.
- `GET /api/tree/export/gedcom?expected_sha256=…&expected_review_token=…`: descarga del
  archivo revisado. El endpoint sin parámetros sigue disponible para clientes anteriores,
  pero no comprueba que hayan revisado un informe.

## Límites que permanecen

- No incluye JPG/PDF, actas, menciones, historial de transcripciones, índices, proyectos,
  búsquedas negativas, tareas, etiquetas, afirmaciones, permisos ni auditoría operacional.
  El informe es una auditoría inicial de la proyección actual, no un inventario exhaustivo
  de todas las pérdidas posibles de cualquier dialecto GEDCOM.
- No garantiza la interpretación por Gramps, FamilySearch u otros programas: falta una
  matriz de ida y vuelta con consumidores independientes y validación completa de dialectos.
  Notas compartidas, estructuras no modeladas, múltiples repositorios por fuente y otros
  campos de archivos originales no se convierten todavía íntegramente al modelo semántico.
- Persisten límites de campos del modelo, normalización de nombres/fechas y decodificación
  ANSEL de mejor esfuerzo. Conservar el original; no anunciar conversión sin pérdidas.
- El informe no acredita que una cita sea verdadera ni permite comprobar los JPG ausentes.
  Tampoco conserva los UUID y enlaces locales a actas, imágenes, revisiones o afirmaciones.
- La ruta clásica sigue generando texto en memoria y en petición HTTP. La nueva ruta
  `/tree/exports` genera una instantánea consistente en segundo plano, con límites de
  capacidad y GEDZIP opcional. La exportación/restauración nativa completa y las pruebas
  de gran escala siguen pendientes. Las huellas de la ruta clásica no constituyen un
  snapshot transaccional de todo el espacio.
- GEDCOM 7/GEDZIP se rechaza en la ruta clásica: utiliza la [importación revisada](IMPORTACION_ARBOL.md).
  No basta cambiar la versión del encabezado. La [guía oficial de migración](https://gedcom.io/migrate/)
  documenta diferencias de estructuras y valores entre ambas versiones.

Verificación histórica de la reparación 5.5.1 (recuentos actuales en
[estado de validación](ESTADO_VALIDACION_REAL.md)). Pruebas: `backend/checks/test_gedcom_evidence.py`,
`backend/tests/test_gedcom_evidence.py` y `frontend/e2e/gedcom-ui.spec.ts`.
Pasan **242 unitarias**, **28 pruebas de navegador**, **40 de scripts** y typecheck/build.
La suite general de integración de esta entrega terminó con **150 correctas y 2 fallidas**
(multipart MinIO), sin omisiones, en 230,34 s. Tras los ajustes finales de referencias,
huella del informe y fuentes descriptivas, se repitieron los **9 casos de árbol/fuentes**,
todos correctos. Estos recuentos no se suman entre sí.
La integración utiliza PostgreSQL desechable, datos sintéticos y espacios independientes;
el navegador utiliza respuestas simuladas. No se han exportado ni importado datos personales,
añadido dependencias/migraciones o desplegado cambios.
