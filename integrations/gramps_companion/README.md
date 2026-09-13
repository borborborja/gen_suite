# Gen Suite Companion para Gramps Desktop

Addon instalable para Gramps 6.x que crea una conexión con Gen Suite, exige una línea base
explícita y sincroniza personas y familias sin usar contraseñas ni cookies de Gramps Web.
No necesita instalar paquetes Python adicionales.

## Instalación

1. Copia esta carpeta completa `gramps_companion` al directorio de complementos de usuario de
   Gramps, o instálala con el gestor de complementos apuntando al archivo ZIP que contiene sus
   dos archivos `.py`.
2. Reinicia Gramps y abre **Herramientas → Utilidades → Gen Suite Companion**.
3. En Gen Suite crea una clave de API de escritura para el espacio de trabajo; pégala solo en la
   ventana de la Companion. No se persiste en el archivo familiar de Gramps.
4. Crea la conexión desde la Companion, confirma la línea base y usa **Subir** o **Bajar**.

La Companion no acepta ni resuelve conflictos por sí sola: se muestran y se deciden en la safata
auditable de Gen Suite. Los borrados se representan como tombstones y no ocasionan una eliminación
silenciosa de datos locales.

El protocolo preserva en Gen Suite todos los envelopes de Gramps, también los tipos que la interfaz
web aún no muestra. La proyección editable actual de la Companion es personas y familias; el resto
queda conservado de forma lossless en el registro de sincronización hasta que tenga una proyección
nativa en la suite.
