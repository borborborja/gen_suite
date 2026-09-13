# PDF/A-2b opcional para documentos

## Qué entrega

En Biblioteca → Exportar hay tres derivados independientes: PDF de consulta, PDF/A-2b
validado y paquete BagIt por libro. Ninguno modifica ni sustituye los originales.
El PDF/A conserva el facsímil y un **anexo de transcripción cercable**, con indicación
de revisión humana o lectura de IA. No coloca palabras sobre el manuscrito sin
geometría acreditada y **no ejecuta OCR ni nuevas llamadas de IA** al exportar.

OCRmyPDF convierte una copia con `--ocr-engine none --mode skip --output-type pdfa-2
--optimize 0 --pdfa-image-compression lossless --jobs 1`. No se aplican rotación,
limpieza, eliminación de fondo ni rasterización forzada. La conformidad PDF/A no
certifica la exactitud histórica de las lecturas ni la identidad binaria de las
imágenes incrustadas: los originales siguen siendo la referencia de preservación.

Después, **veraPDF valida explícitamente el perfil 2b**. La aplicación exige un
único archivo, tamaño/ruta coincidentes, terminación normal, conformidad positiva,
reglas/checks superados y ningún fallo en el informe o su resumen. Un código de
salida cero, una etiqueta XMP o la afirmación del conversor no bastan. XML incompleto,
ambiguo, con DTD/entidades o superior a 512 KiB se rechaza.

También se comparan el número de páginas, sus dimensiones redondeadas a 0,1 pt y
el texto por página en su orden, normalizado a Unicode NFC y espacios simples. Una
diferencia impide publicar. Es un control de estructura/texto, **no** una comparación
visual exhaustiva de cada píxel ni una comprobación de precisión genealógica. Por sí
solo no detectaría el intercambio de dos imágenes sin texto y de igual tamaño.

## Activación: sigue siendo Docker Compose

Las dependencias adicionales fueron autorizadas para este perfil opcional. No se
instalan en el host ni en las imágenes habituales de API/worker. Se fijan
OCRmyPDF **17.11.0** y veraPDF **1.30.2**; el ZIP oficial de veraPDF se verifica con
SHA-256 fijado en `docker/backend.Dockerfile`. Ghostscript, Java y las fuentes/mapas
necesarios proceden de los paquetes de la distribución; no se desactivan actualizaciones
de seguridad. El informe registra también la versión de Ghostscript utilizada.

Con el código actualizado y la configuración existente conservada:

```bash
docker compose --env-file config/.env --profile pdfa up -d --build --wait --wait-timeout 180
```

Para mantenerlo activado en arranques posteriores, añade `pdfa` a `COMPOSE_PROFILES`
en tu configuración: `bundled-storage,pdfa` con MinIO integrado, o `pdfa` con S3
externo. Conserva los demás perfiles que utilices. No regeneres contraseñas ni
copies `.env.example` encima de tu configuración. El asistente `install.sh` también
construye el worker adicional cuando el perfil ya está seleccionado.

Con imágenes publicadas se usa `compose.release.yaml` y el mismo perfil:

```bash
docker compose -f compose.release.yaml --env-file config/.env --profile pdfa up -d --wait
```

**Primero debe existir una release que publique `gen-suite-pdfa` y el backend/frontend
correspondientes.** Estos cambios no publican ninguna imagen ni suponen que la versión
antigua `0.2.0` la contenga. El workflow de release incluye ahora el target adicional.

El worker opcional no tiene puertos públicos, socket Docker, volúmenes de datos ni
tareas generales/cron administrativo. Corre como UID 10001, con raíz de solo lectura,
capacidades retiradas, `no-new-privileges`, un máximo de 256 procesos, 2 CPU y 4 GiB de
memoria. `/tmp` es temporal y tiene límite de 4 GiB; **consume parte de esa memoria**.
Las herramientas nativas no reciben las credenciales DB/S3/IA del entorno del worker.

## Cola, disponibilidad y límites

El outbox duradero envía `export_document_pdfa` únicamente a `arq:pdfa`. El worker
habitual continúa atendiendo el resto de tareas. No hace falta una nueva migración:
se reutilizan `Job`, `TaskDispatch` y `DocumentExport`, con sus políticas RLS existentes.

La API y la interfaz comprueban el heartbeat del worker dedicado, que caduca en
11 segundos y solo empieza después de verificar herramientas/versiones. Sin él,
una nueva solicitud PDF/A devuelve 503; PDF normal y BagIt siguen disponibles.
Una solicitud duplicada devuelve el mismo trabajo activo, aunque el worker esté
temporalmente ausente. El heartbeat no garantiza que el próximo documento supere
la validación. Un corte posterior puede dejar la tarea esperando; consulta Actividad.

Límites de aplicación configurables en el worker:

| Variable | Valor inicial | Alcance |
| --- | --- | --- |
| `PDFA_MAX_PAGES` | 1000 | Imágenes por libro |
| `PDFA_MAX_BYTES` | 2147483648 | Suma declarada de imágenes y tamaño máximo de cada PDF generado |
| `PDFA_TIMEOUT_SECONDS` | 1800 | Cada proceso de análisis/conversión/validación |

Un libro grande necesita espacio simultáneo para entrada, salida, fuentes y
temporales del conversor. Los límites **no certifican** que un libro de 2 GiB quepa
en este perfil. Empieza con libros pequeños y mide; divide por volúmenes o ajusta
conjuntamente memoria, `/tmp`, CPU y límites en un overlay Compose propio. No se ha
acreditado todavía el rendimiento con miles de manuscritos reales.

Se actualiza el progreso durante los procesos, se consulta la cancelación y se mata
el grupo de procesos antes de retirar temporales. Ante errores, timeout o incumplimiento
no se publica un PDF/A válido. Un apagado/OOM puede necesitar el reaper y revisión de
Actividad. Si falla después de comenzar una transferencia S3, se conserva el recibo
incierto según la cuota general; no se regala espacio ni se borra automáticamente.

## Evidencia y descarga privada

El manifiesto guarda la instantánea de transcripciones, versiones de herramientas,
checksums de entrada/salida, comprobación de texto/páginas y el XML completo de
veraPDF con su propio SHA-256. El informe se conserva en PostgreSQL con el derivado;
el PDF se transfiere por la capa habitual de almacenamiento y sus reservas de cuota.

- `POST /api/documents/{id}/exports` con `{"kind":"pdfa"}` crea la tarea.
- `GET /api/documents/{id}/export-capabilities` informa de disponibilidad.
- El listado existente devuelve `kind=pdfa`, `pdfa=validated` y `text_mode=appendix`.
- `GET /api/documents/{id}/exports/{export_id}/content` descarga el PDF con Range.
- `GET /api/documents/{id}/exports/{export_id}/validation` descarga el recibo JSON
  con XML y checksums; devuelve 409 si el checksum no corresponde al derivado.

Todas las rutas requieren autenticación y pertenencia al espacio propietario, incluso
si el documento fuente es público. No hay enlaces públicos a los informes. Una
transcripción posterior marca el derivado como obsoleto; no reescribe su PDF ni el
informe. El informe contiene datos de procedencia: trátalo como documentación privada.
PDF/A y BagIt por libro **no sustituyen** la copia conjunta PostgreSQL+objetos ni una
restauración verificada. No son todavía exportación/importación portable del espacio.

## Comprobación y estado de validación

La construcción del target `pdfa` ejecuta una prueba sintética como usuario sin
privilegios. CI repite esa prueba con raíz de solo lectura y sin red:

```bash
docker build --target pdfa -f docker/backend.Dockerfile -t gen-suite-pdfa:check .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 --memory 4g \
  --tmpfs /tmp:size=4g,mode=1777,nosuid,nodev \
  gen-suite-pdfa:check python checks/smoke_pdfa.py
```

No requiere documentos personales, base de datos, S3 ni proveedor de IA. Genera un
facsímil y dos páginas textuales sintéticas, exige conformidad real, comprueba
acentos catalanes, etiquetas de revisión, búsqueda, color de muestra, checksums y
rechazo de un informe negativo. Faltar herramientas causa fallo, no un test omitido.

En este entorno **pasa la prueba con OCRmyPDF/veraPDF/Ghostscript reales**, instalados
solo en una carpeta temporal, y pasan las comprobaciones de API con dobles, interfaz y
configuración Compose. También pasa `tests/test_pdfa_runtime.py` con PostgreSQL 16,
Redis/ARQ y S3 reales aislados: carga HTTP de dos imágenes, outbox, worker dedicado,
conformidad 2b, recibo con checksum, descarga Range y corrección que marca el PDF
como obsoleto sin reescribirlo ni alterar los originales. No llama a IA.

La prueba se habilita con `GEN_SUITE_RUN_PDFA_INTEGRATION=yes`; exige BD y buckets
de pruebas, y herramientas reales. CI la incluye en la imagen PDF/A contra servicios
desechables. **No se ha construido/arrancado la imagen aquí:** el socket Docker
deniega acceso. Quedan CI/Compose aislado, libros representativos, cancelación bajo
carga, límites y fidelidad visual. Los problemas del MinIO comunitario utilizado
en este ensayo impiden acreditar producción: [estado real](ESTADO_VALIDACION_REAL.md).

## Referencias técnicas

- [OCRmyPDF: conversión sin OCR y modos](https://ocrmypdf.readthedocs.io/en/latest/cookbook.html).
- [veraPDF: selección explícita de perfil e informes](https://docs.verapdf.org/cli/validation/).
- [Distribución oficial veraPDF 1.30](https://software.verapdf.org/releases/1.30).
- [Instalador automatizado oficial de veraPDF](https://github.com/veraPDF/veraPDF-apps/blob/integration/docker-install.xml).

Las imágenes conservan los avisos/licencias de sus paquetes. La revisión de licencias
y vulnerabilidades del conjunto distribuido sigue siendo parte del proceso de release.
