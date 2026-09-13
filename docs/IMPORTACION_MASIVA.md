# Importar y reanudar colecciones de originales

La biblioteca incluye **Importación masiva / reanudar colección**. Esta vía sustituye
el envío de un libro entero en una petición por un manifiesto persistente y fragmentos
de 8 MiB. No transforma ni borra los originales y no consume servicios de IA.

## Navegador

1. Abrir la aplicación mediante HTTPS (o localhost), entrar en el espacio y elegir la
   importación masiva. Indicar título, origen y posibles personas vivas.
2. Seleccionar una carpeta completa. Cada carpeta con imágenes forma un libro; cada PDF
   forma un libro independiente. Las imágenes siguen un orden natural estable, con
   números comparados como números y sin depender del idioma del sistema operativo.
3. Guardar el manifiesto y enviar. Se comprueba SHA-256 por fragmento y, al ensamblar,
   el archivo entero. Cada libro aparece privado en la biblioteca solo cuando todos sus
   originales están verificados. Los PDF se rasterizan mediante otro trabajo persistente.
4. Para pausar, detener el envío o cerrar el navegador. Para reanudar, elegir la sesión
   guardada y volver a seleccionar **la misma carpeta completa**, con el mismo nombre,
   rutas, tamaños y fechas de modificación. El navegador requiere esta selección por
   seguridad: la aplicación no conserva acceso permanente al disco local.
5. Si ya llegaron todos los bytes, **Verificar / reintentar libros sin volver a subir**
   reanuda el procesamiento. Los libros ya publicados no se duplican. Una imagen no
   válida requiere corregir el origen en una sesión nueva; nunca se reemplaza a escondidas.

Los metadatos del manifiesto son inmutables tras declararlos. Las sesiones, archivos,
fragmentos y libros solo son accesibles dentro de su espacio, aunque después se publique
un documento. La transferencia siempre atraviesa el backend autenticado.

## Carpetas del servidor: seguir usando Compose

Crear una clave API del espacio con rol de investigación. No escribir la clave en la
línea de comandos, en el historial ni en un archivo del proyecto. En una terminal local:

```bash
read -rsp 'Clave API del espacio: ' GEN_SUITE_IMPORT_TOKEN
export GEN_SUITE_IMPORT_TOKEN
export GEN_SUITE_IMPORT_PATH=/ruta/absoluta/a/Batejos
docker compose --env-file config/.env -f compose.yaml -f compose.import.yaml --profile import run --rm --no-deps gen-suite-import
```

El comando anterior **solo comprueba la carpeta**. El contenedor opcional recibe los
originales montados en modo solo lectura y la clave API, sin las credenciales de PostgreSQL
ni S3. No se inicia con la instalación normal. Para enviar:

```bash
docker compose --env-file config/.env -f compose.yaml -f compose.import.yaml --profile import run --rm --no-deps gen-suite-import /originals --root-label Batejos --base-url http://gen-suite-backend:8000/api --allow-http --record-type baptism --upload
```

Anotar el identificador de sesión que imprime. Para continuar, repetir el comando con
`--session-id IDENTIFICADOR` y el mismo `--root-label`. La etiqueta conserva las mismas
rutas que el selector de carpetas del navegador. `--allow-http` se limita aquí a la red
interna de Compose; una API remota debe utilizar HTTPS. Los enlaces simbólicos se rechazan,
incluido si se sustituye un directorio durante la lectura.

Para ejecutar el cliente desde el host, no necesita paquetes externos:

```bash
python backend/app/ingest_import.py /ruta/absoluta/a/Batejos --base-url https://suite.example/api --record-type baptism --upload
```

`--origin`, `--municipality` y `--may-contain-living` fijan los metadatos comunes.
Sin `--upload` no se realiza ninguna conexión ni modificación. Tras el uso, retirar
`GEN_SUITE_IMPORT_TOKEN` del entorno de la terminal.

## Límites y capacidad

- Hasta 100.000 originales por sesión; manifiesto paginado en lotes de hasta 1.000.
- Hasta 256 MiB y 64 millones de píxeles por imagen; TIFF/WebP multipágina no se aceptan
  como una sola página. Hasta 8 GiB por PDF; cifrados o sin páginas se rechazan.
- `INGEST_TENANT_QUOTA_BYTES` reserva por defecto 500 GiB de originales por espacio.
  Cuenta las reservas de las sesiones, incluidas las pendientes y completadas. `0046`
  permite liberar las reservas de cargas nunca procesadas, pero solo tras terminar su
  limpieza explícita; las reservas de originales ya procesados se conservan.
  No es una medición del uso físico total: las cargas antiguas y las exportaciones quedan
  fuera de esta cuota. Los fragmentos y las copias verificadas necesitan espacio adicional.
- Se conserva el área de fragmentos mientras no se autorice su [limpieza explícita](LIMPIEZA_IMPORTACIONES.md).
  El filtro de cargas inactivas no las borra automáticamente. La limpieza registra las
  versiones exactas, comprueba originales conservados y puede reanudarse con el mismo
  trabajo. No activar importaciones superiores al espacio físico y temporal disponible.
- El ensamblado lee bloques de 1 MiB y usa un archivo temporal por original. Un fallo
  mantiene los libros ya terminados y permite reintentar; no equivale a un backup completo.

Migración aditiva `0041`; actualizar API y worker conjuntamente. No requiere backfill.
La validación con PostgreSQL/RLS, S3 y Compose reales sigue siendo una puerta de salida
obligatoria; las pruebas de manifiestos de 100.000 archivos no la sustituyen.

## Pruebas reproducibles sin datos reales

```bash
python -m unittest discover -s backend/checks -v
python backend/checks/benchmark_ingestion.py --files 100000
```

El ensayo de directorio crea entradas JPG sintéticas en una carpeta temporal (enlaces
duros a una imagen por libro), comprueba el escaneo y el manifiesto y retira solo sus
fixtures. No mide el rendimiento de una importación real con PostgreSQL/S3.

Desde `frontend`, `npm run test:ingestion` ejecuta tres pruebas del cliente y una de
Chromium que simula un fallo de red, recarga la página y verifica que no se reenvían
originales confirmados. Arranca un Vite exclusivo en localhost:5187 e intercepta todas
las peticiones de API: no puede utilizar datos de una instalación existente. Requiere
el navegador de pruebas de la dependencia Playwright ya declarada.
