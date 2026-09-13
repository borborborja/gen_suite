# Sincronización con Gramps: contrato de la Companion

La integración directa no usa un export/import implícito. Cada instalación de Gramps Desktop y
cada Gramps Web se registra como una conexión de un espacio de trabajo; antes de mover cambios
debe confirmar una línea base explícita. Esto impide que un primer contacto mezcle por accidente
dos árboles parecidos.

## Seguridad y autoridad

- La Companion usa un token de API de escritura del usuario; no se almacenan contraseñas de
  Gramps ni cookies de Gramps Web en Gen Suite.
- Solo `owner`, `admin` y `editor` pueden configurar la sincronización o resolver conflictos.
- `contributor` presenta propuestas; no puede modificar el árbol aprobado ni ejecutar sync.
- Un borrado llega como `tombstone`: queda visible y revisable, nunca se elimina silenciosamente.

## Protocolo

1. `POST /api/interop/connections` crea un peer `gramps_desktop` o `gramps_web`.
2. La Companion enumera el árbol, calcula un SHA-256 canónico y envía los contadores por tipo a
   `POST /api/interop/connections/{connection_id}/baselines`.
3. Cada cambio se transmite por lotes a `POST .../changes` como un envelope con:

   ```json
   {
     "source_system": "gramps_desktop",
     "object_type": "person",
     "object_key": "canonical-stable-key",
     "operation": "upsert",
     "remote_revision": "optional-peer-revision",
     "payload": { "raw": {} }
   }
   ```

4. `POST /api/interop/connections/{connection_id}/apply` proyecta los cambios no conflictivos al
   árbol: personas, familias, lugares, eventos, repositorios, fuentes y citas. Si una familia,
   evento o cita llega antes que sus referencias, queda en cola y se reintenta en la siguiente
   aplicación; no se inventan vínculos.
5. `GET /api/interop/connections/{connection_id}/snapshot` devuelve una instantánea canónica del
   árbol para que Desktop o Web puedan aplicar los cambios de Gen Suite. Usa el identificador
   original del peer cuando ya se conoce, y genera un identificador estable `gensuite:` para los
   objetos creados aquí.
6. Dos cambios sin resolver para el mismo `object_key` y tipo, procedentes de peers distintos,
   pasan a `conflict`. Un editor elige `accept_remote` o `reject_remote` en
   `POST /api/interop/changes/{change_id}/resolve`.

No hay un merge automático de nombres, fechas, parentescos o citas. El estado queda en una
safata de entrada auditable hasta que una persona lo decide.

## Cobertura del modelo

`GET /api/interop/model` publica los tipos soportados por el envelope: `person`, `family`,
`event`, `name`, `place`, `source`, `citation`, `repository`, `media`, `note`, `tag`, `address`,
`url`, `attribute`, `association`, `lds_ordinance` y `custom`. La Companion debe preservar los
campos no interpretados dentro de `payload.raw`, manteniendo la filosofía de round-trip de
Gramps y GEDCOM.

## Companion de escritorio

La Companion instalable está en `integrations/gramps_companion/`. Es un addon `TOOL` de Gramps 6
sin dependencias externas: registra `GenSuiteCompanion.gpr.py`, crea una conexión, confirma la
línea base, sube cambios y aplica la instantánea descargada. Usa únicamente una clave API `gsk_`
de escritura, que no se guarda en el árbol de Gramps.

La proyección editable actual de la Companion es personas y familias. El API conserva sin pérdida
los envelopes de los demás tipos de Gramps y los vuelve a incluir en su instantánea; esa separación
evita destruir información no modelada mientras se completa su editor nativo.

Este contrato permite que la UI de la Companion y una integración de Gramps Web compartan el
mismo protocolo sin acoplar Gen Suite a una versión o credencial concreta de Gramps Web.
