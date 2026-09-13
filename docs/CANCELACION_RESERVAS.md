# Cancelación auditada de reservas de almacenamiento no iniciadas

Entrega `0049` + `0050`: permite cerrar recibos `reserved` abandonados antes de empezar
S3, sin borrar originales, repetir escrituras ni modificar costes de IA. Complementa
la [cuota de almacenamiento](CUOTA_ALMACENAMIENTO.md); **no resuelve todavía todos los
resultados S3 inciertos**. Implementado y verificado localmente con datos sintéticos,
pendiente de validación PostgreSQL/S3 real.

## Cuándo utilizarlo

Una caída entre confirmar la reserva y empezar la transferencia puede dejar bytes
reservados aunque no se haya enviado ningún objeto. Ese recibo puede impedir terminar
un recuento protegido. Ahora el administrador del espacio puede revisar y cancelar
**ese recibo concreto** desde Ajustes.

La operación exige `state=reserved`, sin `UploadId`, tamaño liquidado ni versión
guardada, y que el recibo pertenezca al destino actual. No consulta HEAD para inferir
ausencia: se apoya en el estado duradero previo a cualquier solicitud de escritura.
Los estados `sending`, `unknown`, `succeeded` y `quarantined` no son cancelables por esta vía.

## Flujo en la interfaz

1. Abrir **Ajustes > Cuota global de almacenamiento > Reservas y resultados pendientes**.
   El listado pagina de 20 en 20.
2. En un recibo reservado, elegir **Revisar cancelación de reserva no iniciada**.
3. Indicar un motivo de al menos diez caracteres y pulsar **Revisar efectos de cancelación**.
   La revisión no cambia cuota, trabajo, S3 ni costes. Muestra qué reserva se liberaría.
4. Confirmar explícitamente la cancelación. Cambiar el motivo invalida la revisión y
   obliga a comprobar los efectos de nuevo. Una reserva que haya empezado desde la
   revisión devuelve conflicto y mantiene sus bytes.
5. Consultar **Auditoría de reservas canceladas**: recibo, motivo, autor, fecha y bytes
   de reserva liberados. Hay paginación para acceder al historial completo.
6. Si había un recuento detenido esperando reservas, reintentar **el mismo recuento**.
   La cancelación de una reserva no reanuda trabajos automáticamente.

No se elimina el recibo original. El contador `used_bytes` no disminuye: solo se retira
la reserva que nunca inició S3. Cancelar esta reserva tampoco cancela toda la importación
o investigación; reanudar su trabajo puede generar otra reserva para una operación nueva.

La transición comparte los bloqueos de cuota y recibo con `begin()`. Si la cancelación
gana, un emisor tardío no puede iniciar ese recibo. Si gana `begin()`, la cancelación se
rechaza. El cambio de estado, el contador y la auditoría se confirman juntos en la
transacción HTTP, no en pasos independientes.

La solicitud tiene identidad idempotente: si se pierde la respuesta HTTP, repetir la
misma confirmación devuelve la misma auditoría, sin liberar bytes dos veces. Cambiar
recibo, motivo, comparación o confirmaciones con esa identidad devuelve conflicto.

## Caso especial: respuesta de IA pagada

Una reserva sin escritura S3 no significa que el proveedor de IA no haya recibido una
petición. Por eso no basta con cancelar una reserva de bytes ni con que haya pasado tiempo.
Para una reserva vinculada a `ai_calls` se exige todo lo siguiente:

- La llamada fue creada con la guarda de ejecución nueva (`ai_guard_version=1`). No
  se atribuye esa protección a llamadas históricas mediante un backfill automático.
- La llamada está **conciliada económicamente**, sin respuesta guardada. Una llamada
  `inflight`, `unknown` o con resultado persistido no es elegible.
- No hay una sesión PostgreSQL que conserve la guarda de esa respuesta. La cancelación,
  conciliación y recuperación manual comprueban la misma guarda sin esperar al proveedor.
- El administrador confirma por separado que **renuncia a guardar una posible respuesta
  tardía de esa llamada ya conciliada**. El importe conciliado no cambia y no se repite
  automáticamente la llamada de pago.

Las llamadas nuevas mantienen una guarda de sesión desde antes del envío hasta después
de guardar/liquidar el resultado. Antes de enviar se vuelve a leer el estado económico
y el recibo: un intento suspendido antes del envío no puede revivir una llamada conciliada
o una reserva cancelada. Los resultados existentes se siguen reutilizando sin pagar otra vez.

La guarda ocupa una conexión PostgreSQL, pero no mantiene una transacción abierta durante
el trabajo remoto. La liberación también cubre fallos al confirmar su adquisición; si no
se puede verificar la liberación, se invalida la conexión en lugar de devolverla al pool.
Debe medirse su impacto en conexiones/concurrencia antes de ampliar los workers.

**Límite importante:** una guarda de sesión no demuestra cancelación del proveedor ni
ausencia de ejecución remota después de perder una conexión o reiniciar PostgreSQL.
Por eso siguen siendo obligatorias la conciliación y la aceptación explícita de posibles
respuestas tardías. No se libera por timeout ni se declara gratuita una llamada incierta.
Los bloqueos de sesión terminan al cerrar la sesión; los transaccionales terminan con
su transacción. [Semántica de advisory locks de PostgreSQL](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS).

## Auditoría, permisos y despliegue

`0049` añade la marca de capacidad nullable y `storage_write_actions`: recibo del mismo
espacio mediante clave compuesta, actor, motivo, comparación y bytes liberados. No contiene
credenciales, imágenes ni la respuesta del proveedor. No se modifica ningún recibo histórico.

`0050` es necesaria: la migración `0001` concede edición/borrado por defecto a las tablas
nuevas. Un `GRANT SELECT, INSERT` adicional no retiraría esos privilegios. `0050` revoca
explícitamente `UPDATE`, `DELETE` y `TRUNCATE` para aplicación/PUBLIC y añade políticas
restrictivas que impiden editar/borrar filas. No se modifican migraciones anteriores.
[Privilegios por defecto](https://www.postgresql.org/docs/16/sql-alterdefaultprivileges.html),
[políticas restrictivas RLS](https://www.postgresql.org/docs/16/sql-createpolicy.html).

Los miembros del espacio pueden leer su historial; solo un administrador del espacio
puede revisar o confirmar cancelaciones. Ni rutas ni claves foráneas admiten recibos de
otro espacio. La protección no impide a un administrador de PostgreSQL cambiar datos ni
equivale a una auditoría externa firmada.

Instalación mediante **Compose**, sin dependencias ni servicios adicionales. Desplegar
API y worker conjuntamente con el esquema hasta `0050`; no dejar escritores antiguos
en ejecución. No desplegar esta entrega deteniendo las migraciones en `0049`.

El recuperador admite esquemas hasta `0050`, conserva la auditoría y pone los recibos
pendientes en cuarentena como antes. No los interpreta como permisos para escribir o
liberar bytes en otro destino. Restaurar una copia antigua conserva su esquema histórico:
no se le atribuyen automáticamente las protecciones de `0050`.

## Pendientes que esta entrega no oculta

- Resolver con prueba suficiente los multipart iniciados/interrumpidos, incluida pérdida
  del ACK de creación y partes en vuelo; no se abortan ni liberan aquí.
- Resolver borrados sin versión y PUT enviados cuyo resultado no se pueda acreditar.
  Una lectura de ausencia no excluye una solicitud tardía.
- Tratamiento de reservas de IA históricas sin guarda: se mantienen bloqueadas para
  esta acción hasta un procedimiento de operador seguro, no se reclasifican por antigüedad.
- PostgreSQL/RLS/S3 reales, fallos de conexión y procesos, impacto del pool y escala.
  El resto del [plan completo](IMPLEMENTACION_INVESTIGACION.md) sigue abierto.

## Verificación local

- **213 pruebas backend y 241 subtests**, incluidos 17 contratos nuevos de revisión,
  cancelación, idempotencia, guardas, limpieza de conexiones y permisos explícitos.
- **22 Playwright con API simulada**: tres casos nuevos cubren revisión/confirmación,
  conflicto y aceptación separada para IA con repetición idempotente tras error de red.
- **18 pruebas del instalador/Compose**, typecheck y build correctos.
- SQL offline hasta el único head `0050`, mapeadores, **197 rutas OpenAPI y 15 tareas
  duraderas** registradas. No se añadió trabajo largo inline ni otra tarea de background.
- **142 tests de integración recopilados, no ejecutados**. Las pruebas nuevas preparan
  concurrencia real, recuento desbloqueado, guarda de IA, roles, RLS, padres cruzados y
  privilegios de auditoría. Docker sigue denegando acceso al socket del entorno.

No se han migrado datos reales, cancelado reservas personales, borrado objetos S3,
reiniciado servicios ni efectuado llamadas pagadas durante esta implementación.
