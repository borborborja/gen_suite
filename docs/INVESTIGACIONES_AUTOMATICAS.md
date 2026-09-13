# Investigaciones automáticas reanudables

Estado: implementado en código y comprobado localmente sin servicios externos.
**No certifica producción, exactitud paleográfica ni ejecución real del flujo completo.**
Requiere la migración aditiva `0043` y actualización conjunta de API y worker mediante
Compose. No añade dependencias, servicios obligatorios ni recalcula datos históricos.

## Flujo de uso

1. Importa los libros en la biblioteca y configura en Ajustes los proveedores y, para
   IA alojada, el presupuesto mensual y sus tarifas/límites confirmados.
2. Abre **Investigación → Investigación automática → Preparar investigación**. Selecciona
   libros, etapas, proyecto opcional y un presupuesto total explícito. Cero permite
   procesamiento local y exportaciones sin gasto de IA externo.
3. **Crear plan sin llamadas de IA** guarda la selección, las etapas y los perfiles.
   No encola procesamiento ni contacta con proveedores. Una repetición de la misma petición
   conserva la investigación; cambiar su contenido con el mismo identificador devuelve 409.
4. Revisa los modelos fijados y el presupuesto. Marca la autorización y pulsa
   **Iniciar con este presupuesto**. El worker avanza por etapas duraderas.
5. Puedes **Pausar**, cerrar el navegador y **Reanudar misma investigación** después.
   La pausa impide nuevas admisiones cuando se confirma en BD; llamadas ya admitidas
   pueden terminar y facturarse. No cancela solicitudes remotas ni devuelve dinero.
6. Si necesita atención, revisa la etapa y el registro de llamadas en Ajustes. Explica
   qué has corregido para reintentar. Una ampliación de presupuesto exige administración,
   motivo y la versión actual de la investigación.
7. **Procesamiento terminado** significa que las tareas finalizaron, no que los parentescos
   sean ciertos. Revisa los descubrimientos y sus fuentes antes de aceptar cambios del árbol.

Cancelar conserva originales, resultados, historial y consumo. No es una orden de borrado.
Los miembros investigadores y administradores controlan ejecuciones; solo administración
modifica el presupuesto. Las autorizaciones se verifican en el servidor.

## Etapas y ámbito

Para cada libro se encadenan las etapas seleccionadas: preparar páginas de un PDF,
transcribir pendientes, extraer actas (o leer un documento marcado como índice), indexar
texto/menciones, generar PDF de consulta y generar archivo BagIt por documento. Cuando
terminan todos los libros, se buscan coincidencias para las personas seleccionadas.

La interfaz permite comparar todo el árbol; la API también acepta una lista acotada de
personas. Se capturan sus identificadores al preparar el plan: no se incorporan personas
nuevas a una investigación ya preparada. La búsqueda utiliza **el corpus del espacio**,
no exclusivamente los libros seleccionados. No es un bucle recursivo que acepte familiares
o siga creciendo sin aprobación. La búsqueda familiar especializada anterior sigue separada.

Desde `0044`, los planes nuevos añaden una etapa de índice compatible de todo el corpus
antes de descubrir personas cuando se elige indexar. Usa el mismo presupuesto y perfil,
sin cambiar los planes antiguos ya guardados. La generación resultante puede activarse
después para la búsqueda general. Véase [Índices generacionales](INDICES_GENERACIONALES.md).

Límites actuales: 1.000 documentos y 10.000 personas por investigación; listados y etapas
paginados. Las páginas de un libro no se convierten en miles de trabajos hijos. Hay una
etapa activa por investigación y el coordinador avanza en ticks cortos. La planificación
sintética de 1.000 libros y 1.500 personas genera ahora 7.501 etapas, incluida la nueva
etapa integral del índice; **no es una prueba de
rendimiento de PostgreSQL, imágenes, llamadas externas o 100.000 subidas reales**.

El selector de idioma actual afecta a OCR local. No acredita perfiles paleográficos de
IA para catalán, castellano o latín. El PDF sigue siendo un facsímil con anexo textual:
no es PDF/A ni una capa de palabras con geometría validada. El BagIt por libro no sustituye
una copia completa del árbol, la BD y los objetos.

## Reanudación y contabilidad

- `research_runs` conserva estado, versión, configuración, perfiles y presupuesto;
  `research_run_stages` conserva orden, trabajo, intentos y resultados. Los eventos de
  aplicación registran creación, controles, incidencias, cambios de presupuesto y cierre.
- Crear una etapa guarda su `Job` y la intención de cola en una sola transacción. Una
  reentrega utiliza el mismo trabajo y una nueva generación de envío a ARQ.
- Antes de rearmar una etapa se comprueba el mismo bloqueo de sesión PostgreSQL que
  usa el worker. Mientras el intento anterior siga ejecutándose, no se reencola.
  El detector de trabajos abandonados cuenta la actividad reciente, no la fecha histórica
  de creación de un trabajo recién reanudado.
- Transcripciones corregidas y checkpoints de actas/índices se respetan. La lectura de
  índices no vacía el libro al empezar: solo reemplaza una página tras validar la respuesta
  y comprobar que su transcripción sigue activa. Guarda su revisión y llamada de origen.
- `ai_calls.research_run_id` permite sumar consumo liquidado y reservas de **toda la vida
  de la investigación**, además del límite mensual del espacio. Las reservas inciertas
  no caducan por pausar, cancelar o cambiar de mes.
- Una admisión denegada por presupuesto o incertidumbre detiene la investigación para
  revisión. No amplía presupuestos ni cambia silenciosamente el modelo. Los fallos de IA
  en descubrimiento gestionado no se presentan como una búsqueda completa sin advertencia.
- Reanudar conserva la identidad del trabajo y permite reutilizar respuestas pagadas
  idénticas y verificadas. No garantiza exactamente una ejecución remota. Cambiar el texto
  de entrada puede originar otra petición distinta, siempre sujeta a admisión.

El perfil fija motor, modelo, endpoint y tarifa sin guardar claves descifradas. Rotar el
secreto de una credencial activa es compatible; cambiar las vinculaciones de Ajustes no
reescribe el perfil de una investigación existente. La caducidad bloquea nuevas llamadas,
pero no la recuperación de una respuesta ya pagada. Desde `0045` existe [renovación económica
versionada](RENOVACION_AUTORIZACIONES_IA.md): comparación, motivo y confirmación explícita
por tarea, sin sustituir el perfil original ni reanudar automáticamente la investigación.

El presupuesto controla una estimación conforme a los límites declarados, no la factura
del proveedor. Véase [Control de gasto de IA](CONTROL_GASTO_IA.md). Los envíos Batch nuevos
continúan desactivados hasta gestionar sus reservas y conciliación por solicitud.

## API

| Ruta | Uso |
|---|---|
| `POST /api/research/runs` | Preparar con `client_id`, selección, etapas y `budget_microusd` |
| `GET /api/research/runs` | Lista paginada, sin repetir las selecciones completas |
| `GET /api/research/runs/{id}` | Estado, configuración, perfiles, contadores y balance |
| `GET /api/research/runs/{id}/stages` | Etapas con su trabajo y resultado, paginadas |
| `GET /api/research/runs/{id}/events` | Historial paginado |
| `POST /api/research/runs/{id}/{action}` | `start`, `pause`, `resume`, `retry`, `cancel` |
| `PATCH /api/research/runs/{id}/budget` | Cambio administrativo con importe y motivo |

Los controles y cambios de presupuesto exigen `expected_version`; una petición obsoleta
recibe 409. Las tres tablas nuevas tienen RLS tenant-only y claves foráneas compuestas
para impedir relacionar etapas, eventos o llamadas con la investigación de otro espacio.

## Validación de esta entrega

- **102 pruebas backend y 75 subtests** sin servicios externos: orquestación, bloqueo de
  represa simulado, perfiles, admisión, checkpoints, preservación e importación, entre otros.
- **8 pruebas Playwright** con API simulada; incluyen crear sin iniciar, autorización,
  pausa/recarga/reanudación con la misma identidad y conflicto de versión al ampliar saldo.
- **17 pruebas del instalador**, typecheck y build correctos. Persiste el aviso previo
  de importación estática/dinámica de linkage; bundle inicial de 467,76 kB.
- SQL offline desde `0001` hasta una única cabeza `0043`; API con 175 rutas y las 11
  funciones de envío duradero registradas en el worker.
- **99 tests de integración recopilados, no ejecutados**. Los nuevos casos comprueban
  concurrencia presupuestaria, pausa mientras un bloqueo PostgreSQL real sigue retenido,
  reencolado, detector de abandonos, API y aislamiento de las tres tablas y sus padres.

Docker sigue sin estar disponible para estos ensayos por permisos del socket. No se
aplicaron migraciones, no se usaron datos reales, no se ejecutó IA de pago ni se desplegó.
Es obligatorio ejecutar la integración en servicios desechables antes de publicar.

El [estado general del plan](IMPLEMENTACION_INVESTIGACION.md) conserva los pendientes:
validación a escala real, generaciones de embeddings, benchmark manuscrito, preservación
completa, copias/restauración, interoperabilidad y controles de salida a producción.
