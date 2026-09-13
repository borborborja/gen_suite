# Bloqueo automático de publicación

`docker-release.yml` exige dos validaciones antes de iniciar los jobs que tienen
permiso de publicar imágenes. Se aplica tanto a una release publicada como al
disparo manual del workflow. No despliega la aplicación ni modifica datos.

| Evidencia obligatoria | Jobs que deben terminar correctamente |
| --- | --- |
| `.github/workflows/ci.yml` | `pdfa`, `installer`, `backend`, `frontend` |
| `.github/workflows/recovery.yml` | `full-recovery` |

El script `scripts/verify_release.py` consulta la API de Actions, no nombres de checks
aportados por terceros. Exige workflows activos, un evento **push de main en este
repositorio** y el SHA completo del commit del evento de publicación. Un resultado
de PR, de un fork, de otra rama o de otro commit no sirve. Tampoco sirve una ejecución
manual de recuperación como sustituto del push validado.

Para cada workflow selecciona la ejecución de mayor `run_number` de ese commit,
sin filtrar por estado o éxito. Si está pendiente, fallida, cancelada, omitida o
incompleta, bloquea aunque haya una ejecución verde anterior. Consulta los jobs del
intento exacto y exige todos los obligatorios, sin duplicados; todos los jobs devueltos
deben estar completos y correctos. Si una reejecución parcial no incluye todos,
usar **Re-run all jobs**. Vuelve a consultar ambas ejecuciones antes de aprobar para
detectar cambios de ejecución o intento durante la comprobación.

## Permisos y commit construido

- `verify-release` tiene `contents: read` y `actions: read`, sin `packages: write`.
- Su checkout usa `github.sha` y el script comprueba `git rev-parse HEAD`.
- Solo después del éxito emite el SHA; `build-push` depende de ese job y descarga
  **ese mismo SHA**, no la rama ni un tag que pueda haberse movido.
- Los dos checkouts usan `persist-credentials: false`. El token de lectura llega al
  script por entorno, nunca por argumentos ni por salida.
- El login del registro, los builds y `push: true` pertenecen al job dependiente.
  Sus permisos de publicación no se conceden al job de verificación.

El input manual `tag` sigue siendo una **etiqueta de imagen**, no selecciona el código.
El código es el commit de la referencia elegida al lanzar el workflow; debe tener
la evidencia indicada. Para un tag de release, crear el tag sobre el commit validado.

La API solo se consulta por HTTPS en `api.github.com`, sin redirecciones ni proxies
del entorno. Errores de red, autenticación, límites, JSON no válido o evidencia
incompleta bloquean. Las respuestas están limitadas a 2 MiB y 20 segundos por petición;
más de 100 ejecuciones coincidentes o jobs bloquean en lugar de elegir una página
incompleta. El job tiene un límite de 5 minutos. No hay espera automática ni bypass:
corregir la causa, esperar las validaciones y relanzar la publicación.

Contrato de API utilizado: [ejecuciones de workflows](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#list-workflow-runs-for-a-workflow)
y [jobs de un intento concreto](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#list-jobs-for-a-workflow-run-attempt).

## Validación y límites

Las pruebas de `scripts/tests/test_verify_release.py` usan únicamente respuestas
sintéticas y temporales, sin tokens reales ni red. Comprueban éxito, intentos,
errores, aislamiento de identidad del commit/repositorio, resultados antiguos,
jobs omitidos, cambios durante la consulta, límites, redirecciones, salida segura
y dependencia/permisos del YAML. Las ejecuta el job `installer` de CI junto con sus
pruebas existentes:

```bash
python3 -m unittest discover -s scripts/tests -v
```

Implementado y probado localmente. Cada publicación debe conservar los enlaces de
Actions del commit integrado y exigir rama/tag protegidos, revisión de cambios en
workflows/scripts y permisos de publicación restringidos.
Quien pueda modificar el workflow puede quitar este control: no sustituye esas
protecciones ni una política del registro.

El control acredita una instantánea de validación, no un bloqueo transaccional de
GitHub durante el build. Un cambio de estado posterior requiere revisión operativa.
Tampoco hace atómica la publicación de las tres imágenes ni acredita staging, E2E,
escala, seguridad del almacenamiento o recuperación de datos personales. La regresión
multipart es obligatoria y ya pasa; el aviso de seguridad del MinIO comunitario sigue
bloqueando su recomendación para producción. Consultar [el estado real](ESTADO_VALIDACION_REAL.md) y el
[runbook](PRODUCTION_RUNBOOK.md) antes de publicar.
