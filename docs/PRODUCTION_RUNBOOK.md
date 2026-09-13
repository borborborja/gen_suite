# Runbook de producción

## Gate de release

**Bloqueo vigente:** el MinIO comunitario incluido no acredita el inventario multipart
necesario para la cuota y está afectado por un aviso de seguridad sin corrección
comunitaria publicada. No desplegar esta entrega hasta resolverlo y repetir los gates.
Evidencia y decisiones pendientes: [Estado de validación real](ESTADO_VALIDACION_REAL.md).
El workflow de publicación ya incorpora un [gate automático](GATE_PUBLICACION.md):
exige CI y recuperación correctos de un push a main del commit exacto antes de conceder
el job de publicación. Está probado localmente, **no ejecutado todavía en GitHub**.
No crear releases ni ejecutarlo manualmente mientras persistan estos bloqueos.

1. La rama protegida y el nuevo tag elegido deben tener CI verde: migración limpia, pytest completo y build/typecheck frontend. No reutilizar `v0.2.0` para código nuevo.
2. Desplegar el mismo tag primero en staging con buckets, SMTP y dominio distintos.
3. Confirmar `/readyz`, el flujo de invitación, recuperación, MFA, publicación/revocación y una restauración reciente.
4. Crear el release desde el commit exacto; conservar el tag anterior para rollback de aplicación.

## Actualización a `0048`

- Validar migraciones y [cuota de almacenamiento](CUOTA_ALMACENAMIENTO.md) en servicios
  desechables antes del despliegue. No se ha acreditado todavía esa validación real.
- Actualizar API/worker conjuntamente mediante Compose, con la misma configuración
  `STORAGE_TENANT_QUOTA_BYTES` y todos los demás escritores S3 detenidos. No ejecutar
  simultáneamente versiones antiguas que omitan las reservas; su rollback requiere
  revisar la consistencia de la cuota, no basta reiniciar una imagen anterior.
- Para espacios con datos, iniciar el recuento protegido desde Ajustes. Aceptar que
  bloquea nuevas escrituras y llamadas pagadas, dejando terminar las reservas previas.
  Esperar su finalización o resolver explícitamente el error; no asignar cero a la colección.
- Comprobar cuota, carga, exportación y almacenamiento de respuesta IA con un proveedor
  simulado antes de autorizar gasto. Mantener monitorización independiente del disco/BD,
  multipart, réplicas y backups; la cuota es de contenido por espacio, no de capacidad física.

## Incidente

- Para detener enlaces públicos, establecer `PUBLIC_SHARING_ENABLED=false` y reiniciar backend.
- Para suspender sincronización, mantener `GRAMPS_SYNC_ENABLED=false`.
- Ante credenciales comprometidas: revocar API keys, rotar JWT/SMTP/S3, invalidar refresh tokens y documentar el incidente.
- Si falla `/readyz` dos veces en cinco minutos, retirar tráfico del proxy, revisar logs y restaurar solo en una base aislada.
- Ante falta de espacio, revisar cargas inactivas y usar la [limpieza explícita de staging](LIMPIEZA_IMPORTACIONES.md)
  solo tras conservar los originales y confirmar su alcance. No borrar prefijos de
  documentos ni confundir cuota de admisión con uso físico. Object Lock/retención no se eluden.
- Ajustes permite un [inventario de almacenamiento](INVENTARIO_ALMACENAMIENTO.md) de
  versiones, copias y multipart, sin modificar S3. Su resultado es observacional: no
  acredita espacio libre, integridad ni una cuota activa. Tras una restauración, iniciar
  otro inventario; los informes del origen quedan como históricos invalidados.
- Ante una cuota en `draining`/`counting`, usar **su panel** para reintentar/cancelar, no
  los controles del inventario observacional. Verificar recibos inciertos sin repetir PUT.
  Si no se puede acreditar el resultado, conservar la reserva; cancelar el recuento no
  libera bytes. Para un recibo `reserved` nunca iniciado, usar [revisión y cancelación
  auditada](CANCELACION_RESERVAS.md). Para IA exige conciliación, guarda nueva y aceptación
  separada de respuesta tardía; no usarla con llamadas antiguas sin guarda. Los multipart
  incompletos y resultados enviados inciertos aún requieren resolución segura de operador;
  no borrar filas ni ajustar contadores directamente.
- Una tarifa de IA caducada se [renueva con comparación y confirmación](RENOVACION_AUTORIZACIONES_IA.md).
  No borrar recibos ni sustituir el modelo del trabajo para sortearla. La renovación no
  resuelve costes inciertos, presupuestos agotados o credenciales revocadas: revisar cada
  causa antes de reanudar explícitamente la misma ejecución.

La entrega de cancelaciones requiere **`0049` y `0050`**: esta última revoca permisos
heredados de edición/borrado sobre la auditoría. Validar ambas en servicios desechables,
actualizar API/worker juntos y medir la conexión adicional de guarda por llamada de IA
en ejecución. No confundir la pérdida de una sesión PostgreSQL con cancelación remota.

## Backup y restauración

- El cron diario es solo PostgreSQL; no confundirlo con una copia de los originales S3.
- Copia conjunta, transferencia externa y ensayo aislado: [Recuperación completa](RECUPERACION_COMPLETA.md).
  Requiere ventana de mantenimiento y custodia separada de la clave maestra/configuración.
- El servidor no expone Postgres, Redis ni MinIO. El bucket de backup usa una cuenta separada y no tiene permisos de borrado para la aplicación.
- No restaurar sobre datos existentes ni usar `--clean`. El recuperador exige otro clúster,
  buckets vacíos y deja el destino en cuarentena, con el rol de aplicación sin login.
  Exigir ensayo real con fuentes, imágenes, árbol y aislamiento antes de habilitar servicio.
