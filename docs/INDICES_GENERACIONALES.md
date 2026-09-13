# Índices vectoriales por generaciones

Entrega `0044`: implementación y contratos locales. **No acredita precisión genealógica,
rendimiento a escala ni preparación para producción.** PostgreSQL con pgvector sigue
siendo el motor; no se añade otra base de datos, dependencia ni servicio a Compose.

## Qué cambia

Reconstruir el índice ya no vacía los vectores del corpus. La nueva generación se construye
aparte y el índice activo sigue disponible. Cambiar el proveedor de embeddings en Ajustes
tampoco cambia silenciosamente el modelo con el que se consulta el índice activo.

Los vectores históricos se conservan intactos. Como no tienen un perfil acreditado por
vector, **no se les atribuye un modelo por suposición ni se usan en las nuevas consultas
semánticas**. Después de actualizar será necesario construir y activar una generación.
La búsqueda por palabras, las fuentes y el árbol no dependen de esa operación.

## Flujo de uso

1. Configura embeddings y, si son alojados, confirma la tarifa y presupuesto mensual.
   El perfil debe producir los 1.024 componentes que exige esta integración.
2. En **Ajustes → Generaciones del índice de búsqueda**, pon un título y decide si incluir
   documentos públicos actualmente accesibles, además de tu corpus propio.
3. Autoriza **Construir sin sustituir el índice activo**. Puede consumir IA y espacio
   adicional; sigue sujeto al control de gasto por llamada y su perfil queda fijado.
4. Revisa modelo, trabajo, pendientes y evidencia vigente consultable. Puedes pausar,
   reanudar o reintentar una construcción completa tras resolver su incidencia: conserva
   la identidad del trabajo y su manifiesto, no vuelve a empezar con otro identificador.
5. Revisa cobertura y modelo, explica el motivo y confirma la activación. La sustitución
   es transaccional y auditada. Exige la versión esperada y el índice que creías activo;
   si otra persona lo cambió, responde 409.
6. Para volver atrás, activa la generación anterior con el mismo control. Sus vectores
   se conservan; debe seguir teniendo evidencia vigente y un estado utilizable.

Construcción completa y activación son administrativas. Las generaciones de una
investigación se controlan desde Investigación y solo pueden activarse para búsquedas
generales después de que termine. Cancelar conserva datos y consumos: no cancela por sí
mismo una solicitud ya enviada a un proveedor ni devuelve su reserva.

## Identidad y procedencia

- `embedding_generations`: perfil sin claves descifradas, identidad del espacio vectorial,
  finalidad, estado, índice activo y referencias históricas al trabajo/investigación.
- `embedding_items`: entrada exacta, campos de la mención, SHA-256, revisiones inicial y
  final del acta, documento, página, vector y llamada de IA de origen. Identificador
  determinista y clave única impiden insertar dos veces la misma fuente en una generación.
- `embedding_index_events`: creación, finalización, controles y activaciones con motivo.
  Es un registro de aplicación, no una auditoría externa inalterable.

Las tres tablas tienen RLS tenant-only y relaciones compuestas con su generación. Las
referencias a fuentes son históricas: cada consulta vuelve a unir evidencia actual bajo
su propia RLS. Comprueba el texto/campos utilizados, las revisiones activas del inicio y
final del acta y su estado. Actas rechazadas o sustituidas quedan fuera.

La identidad matemática incluye motor, endpoint, modelo, dimensión y preparación del
texto. Otra tarifa/clave no cambia ese espacio, pero una tarea incremental solo reutiliza
el índice activo cuando coincide **todo** el perfil de admisión: la reentrega no cambia la
huella de una respuesta pagada. Conviene usar modelos versionados cuando existan; guardar
un identificador no impide que un tercero cambie lo que sirve detrás de un alias.

Las consultas utilizan el perfil de su generación. Resultados semánticos/híbridos incluyen
generación, elemento, huella y llamadas de indexación/consulta. Las coincidencias guardan
esa procedencia junto a la puntuación; la recuperación por nombres se distingue de la
semántica. Ningún resultado confirma por sí solo un parentesco.

Se rechazan vectores incompletos, de dimensión incorrecta, nulos, no finitos o que dejan
de ser representables como float32. No se truncan ni rellenan dimensiones. Una respuesta
pagada inválida conserva su consumo y no se reenvía silenciosamente como otra llamada.

## Documentos públicos y correcciones

Incluir documentos públicos es una decisión explícita de coste y alcance. Su información
derivada se captura en el índice privado del investigador. Cada consulta vuelve a comprobar
el acceso: retirar una publicación impide que esa copia vectorial devuelva la fuente.
Esto **no** implementa todavía la purga por retención de todas las copias derivadas tras
retirar una publicación. Revocación de lectura y eliminación física son cosas distintas.

Corregir no dispara gasto automático. La búsqueda por palabras usa la revisión nueva y
los vectores de revisiones anteriores se excluyen. Para incluir nuevos textos, documentos
o fuentes republicadas hay que solicitar otra indexación. La pantalla cuenta entradas
obsoletas/retiradas, no las presenta como evidencia vigente.

## Investigación e incrementos

Los planes nuevos (`pipeline_version=2`) añaden **Completar índice del corpus** antes de
buscar personas cuando se eligen indexación y descubrimiento. Las etapas comparten generación
y reutilizan entradas idénticas ya calculadas en ella. La última etapa incluye también
transcripciones/menciones previas del espacio, dentro del perfil y presupuesto total de la
investigación. Los planes anteriores no se reescriben: pueden cubrir solo sus libros.

Una indexación individual puede añadir entradas al índice activo si coincide exactamente
su perfil. No vacía ni sustituye las anteriores; las consultas solo consideran entradas
terminadas y vigentes. Si otra indexación quedó pendiente sobre esas fuentes, no se anuncia
éxito falso: debe reanudarse aquel trabajo o construirse una generación completa nueva.
Es posible volver a un índice anteriormente activado con incrementos pendientes; estos
no se sirven, y se revisa su cobertura antes de confirmar la vuelta atrás.

La captura usa bloques de hasta 128 fuentes y los embeddings lotes de hasta 16, limitados
también por la cota de entrada. Cursor, elementos y progreso se guardan juntos. No se carga
todo el corpus en memoria Python. La captura paginada es una observación duradera, **no una
instantánea serializable** de la biblioteca mientras otras personas la modifican.

## Límites y verificación

Se usa distancia coseno exacta con filtros de generación y evidencia vigente. No se
acredita una latencia sobre 100.000 páginas ni se introduce aproximación sin medir su
recuperación bajo filtros/RLS. Faltan benchmark real, cuotas físicas, retención y pruebas
de fallos PostgreSQL/S3/Redis. Desde `0045` se pueden [renovar tarifas caducadas](RENOVACION_AUTORIZACIONES_IA.md)
sin modificar el perfil de ejecución ni el espacio vectorial. Las etapas de investigación
usan la renovación de su investigación; las consultas generales, la de la generación.

Validación local:

- **115 pruebas backend y 86 subtests** sin servicios externos. Una prueba detectó
  desbordamiento a infinito al convertir a float32; se corrigió y volvió a pasar.
- **10 pruebas Playwright** con API simulada, incluidas construcción sin sustitución,
  activación, vuelta atrás y conflicto concurrente.
- **17 del instalador**, typecheck/build y SQL offline `0001`–`0044` correctos;
  180 rutas OpenAPI y 12 funciones duraderas registradas. Bundle inicial de 469,25 kB;
  persiste el aviso previo de importación estática/dinámica de linkage.
- **104 tests de integración recopilados, no ejecutados**: construcción/fallo/reanudación,
  activación, cambio de modelo, correcciones, publicación retirada, coseno y aislamiento.
  Los proveedores están simulados también en esos tests de PostgreSQL real.

Docker deniega el acceso a su socket; tampoco hay herramientas PostgreSQL locales para
levantar servicios desechables. No se aplicaron migraciones, no se tocaron datos reales,
no se desplegó y no se ejecutó IA de pago.

La migración es aditiva y no realiza backfill. Antes de desplegar por Compose, validar el
esquema completo y su actualización desde `0043` en servicios desechables, ejecutar la
integración y comprobar una copia restaurable. Véase el [estado del plan](IMPLEMENTACION_INVESTIGACION.md).
