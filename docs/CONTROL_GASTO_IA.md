# Control de gasto y recuperación de llamadas de IA

Estado: implementación en código con pruebas locales sin servicios externos. La migración
`0042`, la concurrencia PostgreSQL y la integración completa aún deben validarse en una
instalación desechable. **No es una certificación para producción.**

## Qué cambia para el usuario

La IA alojada necesita ahora dos autorizaciones explícitas en **Ajustes**:

1. Un presupuesto mensual en USD. Vacío desactiva nuevas llamadas de pago; cero no
   autoriza gasto. La subida de documentos y el árbol no dependen de una tarifa de IA.
2. Un perfil de tarifa y límites en cada vinculación de transcripción, inferencia y
   embeddings. Selecciona una credencial guardada y confirma el modelo exacto, su ventana
   de contexto, salida máxima, techos de precio de entrada/salida, fuente y caducidad.

No se importan automáticamente las antiguas aproximaciones por nombre del modelo. No
se inventan precios actuales ni se cambian los modelos del usuario. Las credenciales
siguen cifradas; ni los trabajos ni el nuevo registro contienen sus claves descifradas.

La configuración habitual sigue desplegándose **mediante Compose**, sin nuevos servicios
ni dependencias. No se ha modificado ningún `config/.env` real ni se ha desplegado esta entrega.

## Qué garantiza y qué no

Antes de cada nueva llamada alojada, una transacción independiente guarda una reserva
en `ai_calls`. La admisión serializa el espacio con `FOR NO KEY UPDATE`: evita que varias
páginas admitan simultáneamente el mismo saldo y es compatible con los bloqueos de claves
foráneas de la transacción que está creando actas o candidatos. El control suma:

- estimaciones antiguas registradas en `usage_events` durante el mes UTC;
- llamadas nuevas liquidadas durante el mes UTC;
- **todas** las reservas pendientes o inciertas, aunque procedan de meses anteriores.

La nueva aritmética usa enteros en micro-USD y `Decimal`; no redondea cada embedding a un
céntimo. El importe mostrado como liquidado sigue siendo una **estimación a la tarifa
confirmada**, salvo una conciliación manual con evidencia de facturación.

Se reserva conservadoramente la ventana completa de contexto declarada por el operador,
más el máximo de salida. Para texto/embeddings hay además una cota conservadora por bytes
UTF-8; un lote demasiado grande se rechaza antes de enviarlo. Para visión se reserva la
ventana completa, **no se usa una supuesta equivalencia entre JPG y tokens**. La ventana
declarada debe ser un límite que el proveedor realmente imponga, incluidos tokens ocultos
y de imagen. Los techos de precio deben cubrir tramos de contexto, caché y recargos.

Esto limita la **estimación autorizada por la aplicación**, no la factura de un tercero.
Una tarifa/contexto incorrectos, recargos no contemplados o gasto con la misma clave fuera
de la suite pueden hacer que la factura supere ese importe. No se promete un límite de
facturación externo. Si el consumo comunicado supera el perfil, la llamada queda incierta
y se detienen nuevas admisiones. La respuesta y el consumo observados se conservan cuando
el almacenamiento está disponible.

La reserva de una ventana completa puede ser grande para una página corta. Si el saldo
no alcanza, se rechaza esa llamada sin aumentar automáticamente el presupuesto. La
optimización de estimaciones por modelo requiere pruebas adicionales; no se reduce la
reserva basándose en una media de páginas anteriores.

## Estados y recuperación

| Estado | Contabilidad | Qué permite la aplicación |
|---|---|---|
| `inflight` | Máximo reservado antes de la red | Una sola admisión por huella; no repetir mientras espera |
| `succeeded` | Consumo válido liquidado; diferencia liberada | Reutilizar respuesta privada guardada, incluso si falló la transacción del acta |
| `unknown` | Máximo retenido | Detener nuevas llamadas de pago; recuperar o conciliar con evidencia |
| `reconciled` | Importe verificado por administración | Mantener historial; no inventar una respuesta ni repetir la llamada original |

Una reserva `inflight` de más de 15 minutos también bloquea nuevas admisiones y aparece
en el contador de incertidumbre. **No se libera por antigüedad ni al cambiar de mes.**

Las respuestas se guardan en S3 privado antes de liquidar en PostgreSQL. La huella incluye
entrada, tipo de operación y perfil; dentro de un trabajo añade su identificador. Un
reintento del mismo trabajo reutiliza una respuesta liquidada y verifica su SHA-256.
La búsqueda interactiva puede reutilizar consultas idénticas del mismo espacio y perfil.
Una petición concurrente idéntica todavía en curso recibe 409, sin otra llamada remota.
Las nuevas transcripciones, actas, entradas de índice y adjudicaciones guardan además
el identificador de la llamada que produjo su resultado, sin modificar las evidencias antiguas.

En **Ajustes → Registro de llamadas y conciliación**:

1. **Recuperar respuesta guardada** comprueba el objeto privado si S3 terminó pero falló
   la liquidación en BD. No contacta con el proveedor ni vuelve a transcribir.
2. Si no hay resultado verificable, consulta el consumo/facturación del proveedor y usa
   **Conciliar con evidencia**. Introduce el importe real, la referencia y su explicación.
   Un timeout, un error HTTP o la ausencia de resultado **no demuestran coste cero**.
3. La conciliación es administrativa, queda auditada y no sobrescribe una liquidación
   anterior. Una llamada aún en curso debe llevar al menos 15 minutos; aun así, hay que
   verificar su estado real. Si luego llega una respuesta, no sobreescribe la conciliación.

Desde `0049`/`0050`, las llamadas nuevas mantienen además una guarda de sesión para su
respuesta: mientras exista, la conciliación/recuperación manual se rechaza aunque hayan
pasado 15 minutos. Se vuelve a comprobar el estado antes de enviar al proveedor. Una
reserva de bytes no iniciada solo se [cancela separadamente](CANCELACION_RESERVAS.md),
con conciliación previa y aceptación explícita de posibles respuestas tardías; no cambia
el coste. No se atribuye esta guarda a llamadas históricas ni se interpreta la pérdida
de una sesión PostgreSQL como cancelación del proveedor.

No se promete *exactly-once* remoto: existe una ventana entre reservar y recibir respuesta.
El SDK ya no hace reintentos automáticos y la extracción no hace una segunda llamada
silenciosa cambiando de JSON Schema a JSON Object. Ese formato se elige explícitamente
en el perfil. Una respuesta inválida/truncada se conserva y contabiliza, pero no se aplica
como una transcripción o acta válida.

## Cobertura y límites actuales

Cubierto: visión, extracción de actas, unión entre páginas, lectura de índices, embeddings
de documentos/menciones/reindexación, consultas semánticas y adjudicación de coincidencias.
La mezcla LLM HTTP de transcripciones continúa desactivada hasta tener su propio trabajo
revisable; el helper ya usa el ejecutor gestionado, pero esto no habilita aquella ruta.

Los trabajos de transcripción, extracción, embeddings e índices conservan el perfil de
su **primera llamada admitida** al reentregarse. Una rotación de la clave no obliga a
guardar el secreto en ese perfil; la credencial debe seguir disponible y activa.
Las [investigaciones automáticas](INVESTIGACIONES_AUTOMATICAS.md) ahora fijan los perfiles
al preparar el plan, antes de encolar etapas. Añaden presupuesto total por investigación,
pausa/reanudación y cambios de presupuesto auditados. El control suma gasto y reservas de
toda su vida, sin reiniciarlo cada mes. Desde `0045`, la [renovación económica versionada](RENOVACION_AUTORIZACIONES_IA.md)
permite confirmar nuevas tarifas/caducidad sin cambiar perfiles originales, huellas ni recibos.

Los motores locales no consumen el presupuesto externo. Para impedir eludirlo etiquetando
un endpoint alojado como local, solo se permiten hosts de `AI_LOCAL_ENDPOINT_HOSTS`, sin
claves de nube ni nombres de modelo `cloud`. El operador puede añadir un servicio realmente
local en su configuración Compose; no debe añadir proxies facturados. Esto no controla
el consumo eléctrico ni la configuración interna de un servidor Ollama administrado fuera
de la suite. Los chequeos antiguos de admisión de trabajos pueden seguir rechazando un
trabajo local si el presupuesto mensual ya está agotado; su desacoplamiento es pendiente
en esas rutas antiguas. Las etapas locales de `ResearchRun` no usan esa puerta de admisión
antigua y pueden ejecutarse con presupuesto externo cero.

**Nuevos envíos Batch desactivados**: los trabajos nuevos devuelven 409 y los antiguos
encolados sin identificador remoto terminan con error explícito. Se conserva la lectura
de lotes que ya tienen identificador para no perder sus resultados. Los lotes históricos
requieren revisar su facturación; no están retroactivamente cubiertos por esta reserva.
La reserva por solicitud, subida duradera del fichero, cancelación/conciliación remota y
coste de resultados parciales de Batch siguen pendientes. No se recalculan datos históricos.

La preservación completa, exportación/restauración de todo el registro y la retención/limpieza
de objetos aún forman parte del plan pendiente. No se borran respuestas ni reservas como
atajo para reintentar. El resultado privado por llamada tiene límite de 16 MiB.

Desde `0044`, la reindexación completa construye una generación separada y conserva su
perfil desde la creación. La búsqueda semántica utiliza el modelo del índice activo,
no una nueva vinculación incompatible. Las llamadas siguen pasando por la misma admisión;
véase [Índices generacionales](INDICES_GENERACIONALES.md).

## API

- `PUT /api/providers/bindings`: `params.budget_policy` valida los campos del perfil.
- `GET /api/providers/spend`: consumo liquidado, reservas, llamadas inciertas y presupuesto.
- `PUT /api/providers/budget`: importe entero en céntimos, no negativo; `null` desactiva pago.
- `GET /api/providers/ai-calls?state=unknown&limit=50&offset=0`: registro administrativo paginado.
- `POST /api/providers/ai-calls/{id}/recover`: recuperar desde almacenamiento privado.
- `POST /api/providers/ai-calls/{id}/reconcile`: `cost_microusd`, `evidence`, `provider_request_id` opcional.
- `GET /api/providers/authorizations` y `POST /api/providers/authorizations/preview`:
  historial y comparación de renovación económica por titular/tarea.
- `POST /api/providers/authorizations`: confirmación administrativa con comparación,
  `client_id`, motivo y autorización explícita; no reanuda trabajos.

Ninguna de estas respuestas expone claves ni rutas de almacenamiento. La tabla nueva tiene
RLS tenant-only y prueba de lectura, inserción y actualización cruzadas. Las pruebas reales
se ejecutan **solo** con BD desechable y la autorización de truncado de tests.

## Referencias de implementación

OpenAI documenta `max_completion_tokens` como límite que incluye tokens visibles y de
razonamiento; ese es el parámetro exigido para perfiles generativos OpenAI. Se conserva
el modelo elegido por el usuario. [Contrato oficial de Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create).

Batch tiene ejecución y resultados separados de la petición inicial; no se trata como
una llamada síncrona liquidable al recibir el identificador. [Guía oficial de Batch](https://developers.openai.com/api/docs/guides/batch).

## Validación

Resultado local de la entrega `0042`: **85 pruebas de backend y 60 subtests**, **6 pruebas
Playwright** con API simulada y **17 pruebas del instalador**, todos correctos. Typecheck,
build y generación SQL offline hasta `0042` también correctos. La carga diferida de
Ajustes deja el bundle inicial en 467,70 kB; persiste el aviso anterior de importaciones
estáticas/dinámicas en linkage. No se han hecho llamadas de pago ni aplicado migraciones.
La entrega posterior `0043` amplía la comprobación local a 102 pruebas backend y 75 subtests,
8 pruebas Playwright y SQL offline hasta `0043`; conserva pendientes las pruebas de
integración reales. Véase [Investigaciones automáticas](INVESTIGACIONES_AUTOMATICAS.md).

- `backend/checks/test_ai_admission.py`: contratos, aritmética, reserva previa, respuestas
  inválidas, límites, incertidumbre, cancelación, recuperación y transportes SDK simulados.
- `backend/tests/test_ai_admission.py`: casos preparados de PostgreSQL real (concurrencia,
  bloqueo FK, reentrega, cambio de mes, cancelación y API administrativa); **no ejecutados localmente**.
- `backend/tests/test_rls_isolation.py`: aislamiento real de `ai_calls`, pendiente de ejecución.
- `npm run test:offline`: pruebas de navegador aisladas, incluidas confirmación de perfil
  y conciliación con API simulada. No son E2E contra una instalación desplegada.

Antes de publicar: aplicar desde cero y actualizar desde `0041` en BD desechable, ejecutar
las pruebas de concurrencia/RLS, verificar backup/restauración y hacer una prueba pequeña
con presupuesto y permiso explícitos para gasto real. Nada de ello se da por ejecutado aquí.
