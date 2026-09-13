# Renovación económica sin perder identidad ni resultados

Entrega `0045`. Permite renovar una tarifa caducada de una investigación larga, de una
generación del índice o de un trabajo que ya tenga una llamada admitida. Mantiene Docker
Compose y no añade dependencias. **Implementado y probado localmente; pendiente de validar
la migración y los contratos de concurrencia/RLS en PostgreSQL desechable.**

## Uso

1. Comprueba el motivo de la parada. Una autorización caducada no es lo mismo que una
   credencial revocada, presupuesto agotado, resultado inválido o consumo incierto.
2. En Investigación, abre la investigación y «Renovar autorización económica» para la
   tarea correspondiente. En Ajustes, abre una generación del índice para renovar sus
   consultas generales; desde el registro de llamadas puedes seleccionar el trabajo.
3. Verifica con el proveedor las tarifas máximas de entrada/salida, incluidos recargos.
   Introduce la próxima fecha de revisión, fuente y motivo. No se consultan ni certifican
   automáticamente los precios del proveedor.
4. Pulsa «Comparar antes de autorizar». Revisa el **titular efectivo**, las tarifas,
   caducidad y reserva máxima de una llamada. Un trabajo que pertenece a una investigación
   renueva esa tarea de **toda la investigación**, no solo la página seleccionada.
5. Marca la confirmación y autoriza. Solo puede hacerlo administración del espacio.
   Editar los campos invalida la comparación; un cambio concurrente exige volver a revisarla.
6. Si corresponde, concilia las llamadas inciertas y ajusta el presupuesto en sus controles
   independientes. Después reanuda o reintenta la misma ejecución desde su control habitual.
   **La renovación no encola, inicia, reanuda ni repite ninguna llamada por sí sola.**

Una investigación que sigue avanzando puede cambiar de versión durante la comparación.
Pausarla facilita una revisión estable. Las llamadas ya admitidas pueden terminar durante
la pausa: mantienen su autorización y reserva anteriores.

## Qué cambia y qué permanece

| Elemento | Efecto de renovar |
|---|---|
| Tarifa de entrada/salida, próxima revisión y fuente | Nueva autorización numerada |
| Motor, modelo exacto, endpoint, identificador de credencial | Conservados |
| Ventana de contexto, salida máxima, parámetro de salida y formato JSON | Conservados |
| Perfil original y huellas de peticiones/resultados | Conservados |
| Presupuesto mensual y presupuesto total de investigación | Conservados; controles separados |
| Llamadas admitidas, costes, respuestas guardadas y conciliaciones | No se reescriben |
| Reservas inciertas y posibles operaciones remotas en curso | No se liberan ni repiten |
| Árbol aceptado, evidencias y espacio matemático de vectores | Sin cambios |

Para cambiar modelo, límites o endpoint se prepara otra ejecución o generación compatible;
la renovación económica no es un mecanismo de migración de modelos. Los perfiles locales
no necesitan autorización de gasto externo. Los trabajos independientes sin llamadas
admitidas todavía toman su configuración de Ajustes.

## Titular y prioridad

La admisión aplica la autorización de la investigación si existe `research_run_id`.
En su ausencia, aplica la generación del índice cuando se conoce; finalmente, el trabajo
independiente. La API resuelve los padres de un trabajo antes de mostrar la comparación.
Así no se puede confirmar una excepción por página que el trabajador vaya a ignorar.

Las etapas de una investigación usan su propia autorización incluso al construir o
consultar su generación. Las búsquedas generales posteriores usan la autorización de
esa generación. Una investigación terminada o cancelada no se renueva: el índice que
conserva puede renovarse por separado, excepto si está cancelado.

## Persistencia y concurrencia

- `ai_billing_authorizations` almacena titular, tarea, versión, perfil base SHA-256,
  política económica, responsable, motivo, fecha y huellas de petición/comparación.
- Tabla privada por tenant, con RLS y claves foráneas compuestas tenant+titular. El rol
  runtime solo tiene `SELECT` e `INSERT`: no puede modificar, borrar directamente ni
  truncar aprobaciones. Esto no constituye almacenamiento WORM: el propietario de BD
  tiene privilegios y el borrado de un titular/tenant puede ejecutar su cascada.
- La confirmación bloquea la misma fila Tenant que admisión, liquidación y conciliación.
  Un `client_id` repetido con contenido idéntico devuelve la aprobación original; otro
  contenido se rechaza. Dos aprobaciones concurrentes de la misma comparación no crean
  dos versiones: la segunda detecta que el estado ya cambió.
- Los endpoints no hacen commits intermedios. Aprobación, incremento de versión y evento
  `billing_renewed` se confirman juntos al terminar la petición HTTP.
- La admisión busca primero un recibo existente. Una respuesta pagada y verificada puede
  reutilizarse sin comprobar la nueva caducidad ni hacer otra solicitud al proveedor.
- Para una llamada nueva se lee la última autorización **dentro del bloqueo Tenant**,
  se recalcula la reserva y se aplican los presupuestos y bloqueos por incertidumbre.
  El recibo conserva el perfil original bajo `execution_profile`, la tarifa admitida
  bajo `budget_policy` y la referencia a su autorización bajo `billing_authorization`.
- La liquidación usa la tarifa del recibo, aunque entretanto se apruebe otra renovación.
  Los reintentos del trabajo resuelven el perfil de ejecución original, no la tarifa del
  último recibo, para mantener la huella y poder reutilizar respuestas ya pagadas.

Sigue siendo una estimación autorizada, **no un límite contractual sobre la factura externa**.
Tampoco se promete ejecución exactamente una vez del proveedor. Batch nuevo sigue desactivado.

## API

- `GET /api/providers/authorizations?scope=research_run&target_id=UUID&task_type=inference`:
  titular efectivo, perfil de ejecución, política vigente, caducidad e historial. Admite
  `offset` y `limit` (máximo 100); la interfaz muestra las últimas 20 aprobaciones.
- `POST /api/providers/authorizations/preview`: `scope`, `target_id`, `task_type`, tarifas
  `input_usd_per_million` y `output_usd_per_million`, `valid_until` con zona horaria y `source`.
  Devuelve comparación y `preview_hash`. No escribe ni llama a un proveedor.
- `POST /api/providers/authorizations`: los mismos campos más `client_id`, `preview_hash`,
  `authorize: true` estricto y `reason`. Requiere administración. No permite sobrescribir
  modelo, credencial, formato ni límites mediante campos adicionales.

Los ámbitos son `research_run`, `embedding_generation` y `job`; las tareas son
`transcription`, `inference` y `embedding`. Cada tarea tiene sus propias versiones.

## Actualización y verificación

`0045` crea la tabla de aprobaciones y añade la unicidad compuesta de jobs necesaria para
sus claves foráneas. No modifica recibos ni rellena datos históricos. No se han editado
migraciones anteriores. API y worker necesitan el mismo código y esquema actualizado;
no desplegar este código sobre `0044`. La creación del índice único requiere ensayar el
tiempo y bloqueo de migración en una copia desechable antes de la actualización real.

El recuperador completo acepta copias `0044` y `0045`, preserva el esquema/ACL original y
no migra automáticamente una restauración antigua. Las renovaciones también se copian;
la cuarentena sigue desactivando credenciales y gasto. No habilitar una restauración
`0044` con este runtime antes de migrarla explícitamente y verificarla.

Recuento actual ejecutado con pytest: **131 pruebas backend y 120 subtests**, incluyendo
16 pruebas nuevas de renovación; **12 Playwright con API simulada** y **18 del instalador**.
Typecheck/build y SQL offline `0001`–`0045` correctos, 182 rutas OpenAPI. Bundle inicial
469,25 kB; permanece el aviso previo de import estático/dinámico en linkage.

Se recopilaron **112 pruebas de integración, sin ejecutarlas**. Siete nuevas cubren los
tres titulares, API/idempotencia/concurrencia, tarifas y permisos/RLS reales. Docker del
entorno deniega acceso al socket. Los contratos simulados no prueban RLS, instalación,
facturación externa ni precisión de manuscritos. El plan completo y la salida a producción
siguen pendientes de sus otros bloques y validaciones.
