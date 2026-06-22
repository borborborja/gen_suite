# App de extracción de registros parroquiales + descubrimiento de parientes

## Contexto

Quieres una app que ingiera ~**500.000 páginas** de libros históricos (bautizos, matrimonios,
defunciones, s. XVII–XIX), las transcriba, y que — partiendo de tu árbol (GEDCOM o manual,
~**1.500 personas**) — **extraiga registros estructurados** del texto e **infiera/descubra
parientes**, siempre con procedencia y confirmación humana (sin corromper tu árbol real).

Pediste explorar y **validar** tu arquitectura de 3 capas (IA local de extracción · Postgres+pgvector ·
Neo4j de grafo). El hallazgo central de la exploración: **ya tienes ~70 % construido en
`gen_suite`** (`/Users/borja/Codi/gen_suite`), una plataforma genealógica madura (FastAPI + React +
PostgreSQL+pgvector + Redis/ARQ + MinIO, multi-tenant con RLS). Lo que falta no es la infraestructura,
sino dos subsistemas concretos: **extracción estructurada** y **record linkage**. El plan recomendado
es **extender gen_suite** con esos subsistemas como módulos aditivos.

---

## Veredicto de validación de tu propuesta de 3 capas

| Tu capa | Veredicto | Por qué |
|---|---|---|
| **1. Extracción (IA local, llama.cpp)** | ⚠️ Parcialmente correcta, mal enfocada | Mezcla **dos** problemas: (a) **HTR** imagen→texto y (b) **extracción de entidades** texto→registro. `llama.cpp` (solo texto) no lee imágenes. La HTR **ya existe** en gen_suite (`libros2pdf`: Tesseract/Claude/OpenAI-compatible/Ollama). Lo que falta es (b), y el catálogo de proveedores **ya reserva** un tipo de tarea `inference→text` sin usar para exactamente eso. |
| **2. PostgreSQL + pgvector** | ✅ Correcta — y **ya está hecha** | La imagen de BD ya es `pgvector/pgvector:pg16`. Las transcripciones se embeben en `Vector(1024)` y hay **búsqueda FTS + vectorial + híbrida (RRF)** funcionando (`modules/search/service.py`). |
| **3. Neo4j (grafo)** | ❌ **Descártalo** | Tu "grafo" tiene **1.500 nodos** — ridículamente pequeño para cualquier motor; una **CTE recursiva** en Postgres lo recorre en microsegundos. Las 500k páginas **no son datos de grafo** (son documentos/texto/vectores). Neo4j solo añadiría un problema de **doble escritura/sincronización** contra el Postgres+RLS que ya funciona, sin ningún beneficio a esta escala. |

**Lo que tu propuesta infravalora (y es el verdadero núcleo de valor):**
1. **HTR de caligrafía histórica degradada** = *garbage-in*. El linkage solo puede ser tan bueno como la transcripción.
2. **Record linkage / resolución de entidades** ("descubrir parientes"). Los embeddings vectoriales son **una señal entre varias**, no la solución: los nombres son repetitivos ("Juan García") y la señal discriminante está en **fechas + relaciones** (¿coinciden los padres?), no en la semántica del texto libre. Hace falta un motor probabilístico **transparente** con *blocking* + *scoring* + revisión humana.

**Reencuadre por la escala asimétrica (corpus enorme, árbol pequeño):** no hay que resolver millones de menciones entre sí (carísimo). Hay que **sembrar desde tus 1.500 personas** y *recuperar* del corpus los registros que les pertenecen (y a sus parientes) → es **recuperación + ranking**, y gen_suite ya tiene el sustrato de búsqueda.

---

## Decisiones (tuyas + las que tomo por ti)

- **Base:** extender **gen_suite** (es la mejor opción de futuro, ver justificación abajo).
- **Capa de relaciones:** **solo Postgres** (CTEs recursivas). Sin Neo4j. *(Apache AGE = capricho opcional futuro, no requerido.)*
- **Pipeline en DOS etapas encadenadas (no son alternativas):** `imagen → [HTR] → texto → [LLM] → registro estructurado`. La **etapa 1 (HTR, imagen→texto)** lee la caligrafía; la **etapa 2 (extracción, texto→JSON)** estructura el registro. Un LLM de solo texto **no** sustituye al HTR (no lee imágenes); un HTR **no** estructura entidades. Cada herramienta cubre una etapa. Ver diagrama en §6.
- **HTR / transcripción (imagen→texto, etapa 1):** **doble vía** — (a) **Kraken/eScriptorium** autoalojado (modelos especializados y *entrenables* por mano/época, coste-cero por página, segmentación de líneas) por defecto cuando exista modelo para la mano del corpus; (b) **LLM-vision vía OpenRouter** como *fallback* para manos sin modelo o páginas difíciles. eScriptorium aporta el bucle corregir→reentrenar. Ver §6.
- **Estrategia de arranque del HTR (bootstrap):** el HTR es el **cuello de botella real** (mala transcripción = *garbage-in* que arruina extracción y linkage), y entrenar un modelo Kraken por escribano exige *ground truth* transcrito a mano. Por eso, **M1 arranca solo con LLM-vision** sobre el primer libro (cero coste de setup, transcripción inmediata), mientras **Kraken se entrena en paralelo** sobre ese mismo libro. Se **mide precisión Kraken vs LLM-vision** en ese libro y solo cuando Kraken iguale/supere al LLM en una mano se promueve a vía por defecto para esa mano. Así nunca se bloquea el avance por el setup del HTR local, pero se captura su coste-cero/página en cuanto está listo.
- **IA de extracción:** **cloud vía OpenRouter** (multi-modelo, ya en el catálogo). Con **tiering de modelos** (barato para el grueso, premium para páginas de baja confianza) para controlar el coste a 500k páginas.
- **Embeddings:** OpenRouter no expone `embedding` en el catálogo → seguirán saliendo del proveedor que **ya usas** para las transcripciones (OpenAI o Ollama). Sin cambios.

**Por qué extender gen_suite es la mejor opción de futuro (no solo la rápida):** su modelo ya está diseñado para esto — multi-tenant + RLS, abstracción de proveedores con el hook `inference` sin usar, flags `is_inferred` **ya presentes** en `Person`/`Name`/`Event`, pgvector, cola async, GEDCOM round-trip. Los subsistemas nuevos son **módulos aditivos** (tablas y rutas nuevas), no modificaciones del núcleo → bajo riesgo. Una app separada re-derivaría todo esto. El único riesgo de futuro sería que el modelo no pudiera representar *evidencia vs conclusión*; con `is_inferred` + una tabla `Citation` nueva, sí puede.

---

## Lo que YA tienes en gen_suite (no reconstruir)

- **Fuente/ingesta:** modelos `Document`/`Page`, MinIO, y **conector FamilySearch** (`connectors/builtin/familysearch.py`). *(Tu descargador `fs_d_v3` puede alimentar esto.)*
- **HTR imagen→texto:** `libs/libros2pdf/` + `tasks/transcription_tasks.py` (ARQ, commit por página, progreso SSE, cancelación).
- **Embeddings + búsqueda:** `tasks/embedding_tasks.py` (`embed_texts`, 1024-dim) + `modules/search/service.py` (FTS español + vectorial + híbrida RRF).
- **Modelo genealógico:** `Person`, `Name` (`is_inferred`,`is_primary`), `Family`/`FamilyChild`, `Event` (`is_inferred`), `Place` (dedup por `normalized_key`). **Import/export GEDCOM** round-trip (`modules/tree/`).
- **Infra:** RLS por tenant, ARQ/Redis, `Job` con SSE, proveedores por tarea (`tesseract/ollama/claude/openai/openrouter`), desplegado con tus convenciones de homelab.

---

## Lo NUEVO a construir

### 1. Modelo de datos — migración `0008_records_extraction.py` (`down_revision="0007"`)

Cuatro tablas nuevas en `backend/app/models/`, siguiendo el patrón existente (`uuid_pk`, `TimestampMixin`, `tenant_id`+RLS, JSONB `raw`, pgvector). Copiar el estilo de `0002_genealogy.py` (tabla+RLS+GRANT) y de `0006_search.py` (pgvector/HNSW en SQL crudo). La 0008 también crea `pg_trgm` (aún no existe).

- **`records`** — un acta extraída por página/región: `record_type` (baptism/marriage/death/…), `date_raw`/`date_year`/`month`/`day`, `place_id`→`places`, provenance (`document_id`,`page_id`,`transcription_id`), `raw_json` (extracción LLM verbatim), `summary`, `confidence`, `status` (extracted/needs_review/reviewed/rejected), `visibility`, `embedding Vector(1024)`. RLS con excepción `OR visibility='public'`.
- **`person_mentions`** — cada persona+rol dentro de un acta: `role` (principal/father/mother/godfather/godmother/spouse/spouse_father/spouse_mother/witness/declarant/…), `given`/`surname`/`surname_prefix`/`name_raw`, `sex`, `stated_age`/`stated_origin`/`stated_status`, **claves de blocking** `block_key_surname`/`block_key_given` (fonético español) + `norm_given`/`norm_surname` (sin acentos + *fold* latín→vernáculo), `embedding **halfvec(1024)**` (2 bytes/dim, mitad de espacio a escala de millones), `resolved_person_id`→`persons` (nullable, al confirmar), `match_status`.
- **`match_candidates`** — par árbol↔mención puntuado: `tree_person_id`, `person_mention_id`, `record_id`, `score`, `evidence` JSONB (desglose por señal), `status` (pending/confirmed/rejected), `decided_by`/`decided_at`, `method`. `UniqueConstraint(tree_person_id, person_mention_id)`.
- **`citations`** — procedencia (la columna vertebral del "siempre con fuente"): enlace polimórfico `target_type`/`target_id` (la conclusión: person/name/event/family) → `record_id`/`page_id`/`transcription_id`/`person_mention_id`/`match_candidate_id`. *(Sin FK en `target_id` por ser polimórfico — añadir chequeo de integridad periódico.)*

**Escritura de conclusiones inferidas:** reutilizar `Person`/`Name`/`Family`/`FamilyChild`/`Event` con `is_inferred=True` (los flags ya existen) + una fila `Citation` por cada conclusión. El árbol nunca se modifica salvo por una acción de **aceptar** explícita.

Índices: pgvector **HNSW** (no IVFFlat, tolera inserciones incrementales), `halfvec_cosine_ops` para menciones; GIN trigram sobre `norm_surname`; compuestos `(tenant_id, block_key_surname)`.

### 2. Pipeline de extracción — tarea ARQ `extract_records`

Nuevo `backend/app/tasks/extraction_tasks.py`, **clon estructural** de `transcription_tasks.py` (commit por página, SSE, cancelación, re-aplicar RLS tras commit). Registrar en `worker.py` (`functions=[…, extract_records]`).

- **Helper nuevo** `extract_structured(rc, text, *, schema, system)` en `modules/providers/service.py` (hermano de `embed_texts`): cliente OpenAI-compatible → `chat.completions.create(response_format={"type":"json_schema",…})`; *fallback* a `json_object` + validación Pydantic para modelos sin structured output. Resuelto vía `ProviderService.resolve(task_type="inference")`.
- **Contrato de salida** (Pydantic en `modules/extraction/schemas.py`, fuente de verdad → JSON Schema): `ExtractedPage{has_record, records:[ExtractedRecord{record_type, date, place_raw, parish_raw, summary, confidence, mentions:[ExtractedMention{role, given, surname, name_raw (verbatim, incl. forma latina), sex, stated_age, stated_origin, stated_status}]}]}`. Prompt: texto histórico español/latín de registro; extraer toda persona nombrada y su rol; conservar grafía original en `name_raw`; no inventar; `has_record=false` en páginas en blanco/índice.
- **Flujo:** resolver proveedor `inference` → seleccionar transcripciones del documento con *left-anti-join* contra `records` (reanudable, como `embedding_tasks.py` salta las ya embebidas) → por página: comprobar cancelación, llamar `extract_structured` en `asyncio.to_thread`, insertar `Record`+`PersonMention`s (+ `Place` con el patrón de dedup de `importer.py`), **calcular claves de blocking inline** (Python puro), commit, `pub({"kind":"page_ok",…})`.
- **Tiering de coste** (sin cambios de esquema, vía opciones de tarea): 1ª pasada con `model_cheap` (`google/gemini-2.5-flash-lite`); si `confidence < floor` o el JSON falla o `mentions==[]` → marcar `needs_review` y reencolar esa página a `extract_records_retry` con `model_premium`.
- **Embeddings de menciones** (no inline): generalizar `embedding_tasks.py` con `embed_mentions(document_id)` (mismo bucle `BATCH=16`, `embed_texts`) sobre un string sintetizado `"{name_raw} ({role}) {stated_origin}"`.

### 3. Blocking / normalización fonética española

- **Añadir `abydos`** (Python puro, sin deps nativas) a `backend/pyproject.toml` → **Beider-Morse Phonetic Matching** con `language_arg='spanish'` para `block_key_*` (maneja `x↔j↔g`, `b↔v`, h muda — exactamente la variación ibérica).
- **Diccionario de *fold* latín→vernáculo** (`Joannes→Juan`, `Jacobus→Jaime/Diego`, `Aegidius→Gil`, `Eulalia→Olalla`…) en `modules/extraction/normalize.py`, aplicado a `norm_given` (el clero escribía en latín; tu árbol está en vernáculo).

### 4. Motor de linkage / "descubrir parientes" (sembrado desde el árbol)

Nuevo módulo `backend/app/modules/linkage/` (`router.py`/`service.py`/`schemas.py`/`scoring.py`) + tarea `generate_candidates`.

- **Generación de candidatos** dada una `Person` del árbol: (1) construir semilla con `tree/service.py:get_person_detail` (nombre primario, año nacimiento/defunción, lugar y **nombres de padres/cónyuge**); (2) **blocking** SQL barato (`block_key_surname` + trigram `%` sobre `norm_surname`) → decenas/cientos de menciones, no millones; (3) **ranking híbrido** reutilizando `search/service.py` (RRF FTS+vector) sobre `person_mentions` con la semilla sintetizada.
- **Scoring transparente** (`scoring.py`), desglose guardado en `match_candidates.evidence`:
  - `name_sim` — Jaro-Winkler (añadir `jellyfish`) sobre `norm_*` con *fold* latín, *boost* si coincide la clave fonética.
  - `date_plausibility` — `Record.date_year` vs año esperado según el rol (un principal de bautizo ≈ año de nacimiento; un padre ≈ 20–60 años más).
  - `place_proximity` — `place_id` exacto=1.0; mismo municipio normalizado=0.7; trigram sobre `Place.normalized_key`.
  - `relational_corroboration` (**la señal más fuerte**) — ¿el acta nombra a **los mismos padres/cónyuge** que conoce el árbol? Comparar las **otras menciones del mismo acta** con los parientes de la semilla (obtenidos por **CTE recursiva**). Un bautizo cuyo padre/madre coinciden con los padres conocidos ≈ certeza.
  - `llm_adjudication` (opcional, solo top-K en banda ambigua 0.45–0.7) — `extract_structured` con prompt "¿misma persona? {match,confidence,reasoning}".
  - `score = Σ wᵢ·sᵢ` (p. ej. relacional 0.35 · nombre 0.30 · fecha 0.20 · lugar 0.15); persistir `match_candidate` con `status='pending'` si `score ≥ enqueue_floor`.
- **De match confirmado → parientes nuevos:** al confirmar (árbol ↔ mención `principal`), las demás menciones del acta (padre/madre/cónyuge/padrinos) se proponen como personas nuevas; si ya existen en el árbol → solo `Citation` (corroboración); si no → propuesta en la cola. Al **aceptar**, escribir `Person`/`Name`/`Family`/`Event` (`is_inferred`) + `Citation`. La persona nueva se vuelve **semilla** y se re-ejecuta `generate_candidates` → **volante de descubrimiento**. Nunca auto-merge.

### 5. API + frontend de revisión (humano en el bucle)

- **Backend** `modules/linkage/` (registrar en `main.py:MODULE_ROUTERS`, patrón de `transcription`/`jobs`): `POST /linkage/discover {person_id}` (crea job, devuelve `JobOut`), `GET /linkage/candidates?person_id=&status=`, `POST /linkage/candidates/{id}/confirm|reject`, `POST /linkage/proposals/{id}/accept` (escritura de conclusión + `Citation` en una transacción). **Garantía:** ningún endpoint escribe en el árbol sin un `confirm`/`accept` explícito.
- **Frontend** (React+Vite, patrón feature-folder, `fetch`): `frontend/src/api/linkage.ts` (espejo de `api/transcription.ts`) + `frontend/src/features/discovery/DiscoveryView.tsx` (lista de candidatos con miniatura de la página fuente vía `fetchPageObjectUrl`, desglose de evidencia, botones Confirmar/Rechazar/Aceptar). Progreso vía el polling de job ya existente. Montar en `App.tsx` junto a `DocumentsView`.

### 6. HTR local con Kraken / eScriptorium (motor de transcripción, alternativa/complemento al LLM)

**Recordatorio del pipeline en dos etapas** (las herramientas no compiten, se encadenan):

```
Imagen del libro ──▶ [ETAPA 1: HTR] ──▶ texto plano ──▶ [ETAPA 2: extracción] ──▶ registro estructurado
   (página escaneada)   Kraken/             "Joannes filius      LLM texto             {tipo: bautizo,
                        eScriptorium          Petri Garcia..."    (OpenRouter, §2)       fecha:.., padre:..}
                        · ó LLM-vision (fallback)
```

La transcripción actual de gen_suite es Tesseract (flojo en manuscrito) + LLM-vision (bueno, pero con coste/página y no especializable). **Kraken** (motor HTR open-source para documentos históricos: segmentación de líneas/baselines + modelos **entrenables**) y **eScriptorium** (plataforma web sobre Kraken con UI de transcripción/corrección y entrenamiento de modelos) añaden un motor **local, coste-cero por página y mejorable** para las manos del corpus. Es *upstream* de la extracción (§2): produce `Transcription`, que luego extrae el LLM. **No requiere cambios de modelo de datos** (`Transcription` ya tiene `engine`/`model`/`confidence`).

**Orden de adopción (bootstrap, ver Decisiones):** no bloquear el avance por el setup de Kraken. Etapa 1 arranca con **LLM-vision** en M1; Kraken se entrena en paralelo sobre el mismo libro y se promueve a vía por defecto **por mano** solo cuando iguala/supera al LLM-vision en una métrica medida (CER/WER sobre páginas con *ground truth*). El *fallback* LLM-vision nunca desaparece: cubre manos sin modelo y páginas de baja `confidence`.

- **Catálogo** (`modules/providers/catalog.py`): nuevo engine `kraken` (capability `ocr_local` como tesseract, `requires_key=False`), elegible en `task_type="transcription"`.
- **Rama de transcripción** (`tasks/transcription_tasks.py:transcribe_image`): `if rc.engine == "kraken": return _htr_via_kraken(img, model=rc.model, base_url=rc.base_url)`.
- **Microservicio `kraken-htr`** (NO meter kraken/torch en la imagen del backend — arrastra GBs): FastAPI fino que envuelve la API Python de kraken — `POST /htr {image} → {text, lines:[{bbox,text,conf}], confidence}`. Desplegado como **stack aparte** (red interna, GPU si hay). gen_suite lo llama vía `rc.base_url` interno. Cliente nuevo `modules/transcription/htr_kraken.py`.
- **eScriptorium** como **stack aparte** (su compose oficial con Postgres/Redis propios) para el bucle **transcribir→corregir→reentrenar**. Comparte los `.mlmodel` con `kraken-htr` por volumen; las páginas corregidas reentrenan el modelo.
- **Modelos**: partir de modelos públicos de letra hispánica histórica (procesal/humanística) — HTR-United / Zenodo / repos de eScriptorium — y afinar por parroquia/escribano.
- **(Opcional)** la geometría `lines[].bbox` de Kraken puede poblar `Record.region_bbox` (varias actas/página) y resaltar el recorte en la UI de revisión; guardar ALTO en MinIO o en un `layout JSONB` (migración menor posterior).

**Tiering de transcripción:** Kraken por defecto cuando hay modelo para la mano → *fallback* a LLM-vision (OpenRouter) en manos sin modelo o `confidence` baja. El bucle de corrección de eScriptorium reduce progresivamente ese *fallback* (y el coste).

---

## Hitos

- **M0 — Cimientos:** migración `0008` (4 tablas, RLS, `pg_trgm`, HNSW/halfvec) + modelos + `models/__init__.py`. Deps `abydos`,`jellyfish` en la imagen del worker. Configurar binding `inference` → credencial OpenRouter.
- **M1 — Slice vertical (un libro parroquial):** **etapa 1 con LLM-vision** (transcripción ya existente, sin esperar a Kraken) + helper `extract_structured` + contrato `extraction/schemas.py` + tarea `extract_records` (modelo barato) + `normalize.py`. `linkage` con blocking + Jaro-Winkler + fecha + **corroboración relacional** (sin LLM aún). `DiscoveryView` mínima. **Criterio de salida:** extraer un libro real → sembrar 3-5 personas → confirmar un match → ver un ancestro inferido con imagen-fuente clicable.
- **M2 — Calidad + volante:** embeddings de menciones/records + ranking híbrido; BMPM; propuestas co-mención → parientes nuevos; adjudicador LLM; evidencia en la UI.
- **M3 — Endurecer a 500k:** tiering + `extract_records_retry`; **sonda de coste** sobre 1.000 páginas (tokens/página reales de OpenRouter → extrapolar); ingesta reanudable + barrido `discover-all`; tuning HNSW `ef_search`, validar recall de halfvec.
- **M4 — Después:** deduplicado interno del corpus (misma acta/persona en varios registros). Apache AGE opcional, sin construir.
- **Track HTR (paralelo, desde M1 — bootstrap):** mientras M1 transcribe con LLM-vision, en paralelo: desplegar `kraken-htr` + eScriptorium como stacks homelab; añadir engine `kraken` al catálogo y la rama en `transcribe_image`; seleccionar/entrenar un modelo para la mano del primer libro (corrigiendo en eScriptorium las páginas que ya transcribió el LLM-vision → *ground truth* barato); **medir CER/WER Kraken vs LLM-vision** en ese libro. **Criterio de promoción:** Kraken pasa a vía por defecto **de esa mano** solo si iguala/supera al LLM-vision; si no, sigue LLM-vision. Fijar el umbral de `confidence` para el *fallback*.

---

## Estado de implementación (2026-06-19)

- **Estela (frontend) — HECHO.** `frontend/src/features/estela/` porta el diseño completo (8 vistas, tema claro/oscuro, atajos, toast/undo, zoom, layouts de árbol + abanico) con **datos de muestra**, montado tras el login (`App.tsx` → `<EstelaApp>` con tenant activo). `tsc` + `vite build` limpios. Clientes `api/linkage.ts` y `api/extraction.ts` listos pero aún no cableados a las vistas.
- **M0 — HECHO (preexistente):** modelos `record/mention/match_candidate/citation` + migración `0008` (4 tablas, RLS, `pg_trgm`, HNSW).
- **M1 — HECHO y VERIFICADO end-to-end contra Postgres real:** `modules/extraction/` (normalize, schemas, `extract_structured`, service+router `/extraction/jobs`), `tasks/extraction_tasks.py` (`extract_records`), `modules/linkage/` (scoring transparente, **volante**: `generate_candidates` + `confirm` [con Event inferido] + `reject` + `list_proposals`/`accept_proposal` con **dedup** contra el árbol + enlace de familia idempotente + Citation), `tasks/linkage_tasks.py`, router `/linkage/*` (con `tree_person` en `CandidateOut`). Worker ARQ procesa los jobs. **Frontend DescubrimientosView cableado a la API real** (`mapCandidate.ts` + store). Verificado: fold latín «Joannes»→«Joan» (JW 1.00), corroboración relacional 1.00, score 0.905; flujo discover→confirm→accept con dedup.
- **M2 — HECHO (graceful sin proveedor, verificado):** `embed_mentions` (tarea + endpoint `/extraction/embed-mentions`) ✅; **adjudicación LLM** de la banda ambigua en `generate_candidates(adjudicate=True)` ✅; **recuperación híbrida** — `_vector_retrieve` (coseno sobre `person_mentions.embedding`) unida al blocking fonético/trigram ✅. **Decisión:** descartado abydos/BMPM — empíricamente NO colapsa los casos ibéricos (`Ginés`/`Xinés`, `Joannes`/`Joan`); `spanish_phonetic` los une mejor.
- **Track HTR/Kraken (§6) — HECHO los puntos de integración:** engine `kraken` en el catálogo (`ocr_local`, base_url al microservicio, visible en `/providers/catalog`), rama `if rc.engine=="kraken"` en `transcribe_image`, cliente `modules/transcription/htr_kraken.py`. Microservicio scaffold en `docker/kraken-htr/` (FastAPI sobre kraken + Dockerfile + compose homelab). eScriptorium queda como stack aparte (no scaffolded).
- **M3 — infra de sonda de coste HECHA:** `extract_structured_with_usage` captura tokens; `extract_records` acumula `tokens`/`tokens_per_page` y `projected_tokens_500k` en `Job.result`. Falta correr la sonda real (1.000 págs) con una clave configurada.
- **M4 — deduplicado interno del corpus HECHO y VERIFICADO contra Postgres:** `modules/linkage/dedup.py` — `find_coreferents` (misma persona en varias actas: blocking + `coref_score` con gate de nombre de pila + parientes compartidos + fecha/origen) y `find_duplicate_records` (misma acta extraída dos veces). Endpoints `/linkage/mentions/{id}/coreferents` y `/linkage/documents/{id}/duplicate-records` (vivos). Tests en `test_linkage_scoring.py`. Verificado: «Franciscus Vidal» (1779) co-referente de «Francesc Vidal» (boda 1745) score 0.95 por Maria Soler compartida; falso positivo «Joan Vidal» eliminado por el gate. *(Apache AGE: opcional, no construido — innecesario a esta escala.)*
- **Pendiente:** ejercitar embeddings/adjudicación/extracción/sonda con un binding de proveedor real; entrenar/desplegar Kraken+eScriptorium.

## Escala, coste y riesgos

- **Coste de extracción (dominante):** 500k páginas × 1 llamada LLM. El grueso va en el *tier* barato → **el modelo barato y la tasa de retry premium (`confidence_floor`) son las dos palancas del coste total**, ambas configurables tras la sonda de M3. Throughput limitado por rate-limits de OpenRouter y `WorkerSettings.max_jobs` (hoy 4) — subir concurrencia y *shardear* documentos; el commit por página ya hace las tiradas reanudables.
- **Kraken baja el coste de HTR a ~0/página (§6):** con transcripción local, el coste LLM se concentra en la **extracción** (§2); el *fallback* LLM-vision solo se paga en las páginas que Kraken no cubre bien, y el bucle de corrección de eScriptorium lo reduce con el tiempo. A 500k páginas es la diferencia entre un coste recurrente alto de HTR y casi solo el de extracción.
- **pgvector a millones:** `transcriptions` ya prueba HNSW+1024. Para menciones: `halfvec(1024)` + **blocking antes que ANN** (el índice se consulta con un conjunto pequeño) + `ef_search` por consulta. Sin cambios arquitectónicos.
- **Recorrido del árbol (CTE recursiva):** 1.500 nodos = instantáneo; RLS lo acota solo. Confirma que Neo4j es innecesario:
  ```sql
  WITH RECURSIVE ancestors(person_id, depth) AS (
    SELECT :seed_id, 0
    UNION ALL
    SELECT parent_id, a.depth+1 FROM ancestors a
    JOIN family_children fc ON fc.person_id=a.person_id
    JOIN families f ON f.id=fc.family_id
    CROSS JOIN LATERAL (VALUES (f.husband_id),(f.wife_id)) AS p(parent_id)
    WHERE parent_id IS NOT NULL AND a.depth < :max_depth)
  SELECT DISTINCT person_id FROM ancestors;
  ```
- **Riesgos top:** (1) **HTR de manuscritos degradados = garbage-in** — mitigado por `confidence` + retry premium, pero algunos libros necesitarán corrección humana; mayor amenaza al *recall*. (2) **Variación grafía/latín** (`Joannes/Juan`, `Ginés/Xinés`) — mitigado por BMPM+fold+Jaro-Winkler, nunca perfecto → confirmación humana. (3) **Precisión/recall del linkage** — `enqueue_floor`/pesos configurables, afinar en el libro de M1. (4) **Sorpresa de coste** — acotada por sonda. (5) **Integridad de procedencia** (`Citation.target_id` polimórfico) — chequeo periódico de huérfanos. (6) **Kraken requiere modelo por mano + GPU** y buena segmentación en registros densos/multicolumna; sin modelo adecuado su precisión cae por debajo del LLM → por eso es *doble vía*, no sustitución.

---

## Verificación (end-to-end)

1. **Levantar el stack** local de gen_suite (db/redis/minio + backend + worker), aplicar `alembic upgrade head` (incluye `0008`).
2. **Unit:** `scoring.py` sobre pares sintéticos (`Joannes`↔`Juan`, padres coincidentes → score alto; homónimo con padres distintos → bajo); la CTE recursiva sobre un árbol de prueba.
3. **Integración:** configurar binding `inference`→OpenRouter; ingerir **un libro** (conector FamilySearch o subida) → `transcribe_document` (existente) → `extract_records` → comprobar filas en `records`/`person_mentions` con `raw_json` y claves de blocking.
4. **Flujo de descubrimiento:** `POST /linkage/discover` para una persona semilla → revisar `GET /linkage/candidates` (con desglose de evidencia) → `confirm` un candidato → verificar que aparece un `Person`/`Event` `is_inferred=True` **con `Citation`** a la página, y que la miniatura de la imagen-fuente abre.
5. **Sonda de coste (M3):** `extract_records` sobre 1.000 páginas reales → registrar tokens/página de OpenRouter → extrapolar a 500k antes de comprometer el lote completo.

---

## Ficheros críticos

**A seguir (existentes):**
- `backend/app/tasks/transcription_tasks.py` — plantilla exacta de `extract_records`; además, añadir la rama `kraken` en `transcribe_image` (§6).
- `backend/app/modules/providers/catalog.py` — añadir engine `kraken` (capability `ocr_local`).
- `backend/app/modules/providers/service.py` — añadir `extract_structured` junto a `embed_texts`; `resolve(task_type="inference")`.
- `backend/app/modules/search/service.py` — reutilizar `hybrid_search`/`vector_search` como sustrato de recuperación.
- `backend/alembic/versions/0002_genealogy.py` (tabla+RLS+GRANT) y `0006_search.py` (pgvector/HNSW SQL) — estilo de la `0008`.
- `backend/app/modules/tree/service.py` (semilla + CTE de corroboración) e `importer.py` (write-back Person/Name/Family/Event/Place).
- `backend/app/tasks/worker.py` (registrar tareas) · `backend/app/main.py` (registrar router) · `frontend/src/features/documents/DocumentsView.tsx` + `frontend/src/api/transcription.ts` (patrón de UI/API).

**A crear:** `models/{record,mention,match_candidate,citation}.py` · `alembic/versions/0008_records_extraction.py` · `tasks/extraction_tasks.py` · `modules/extraction/{schemas,normalize}.py` · `modules/linkage/{router,service,schemas,scoring}.py` · `frontend/src/api/linkage.ts` · `frontend/src/features/discovery/DiscoveryView.tsx` · `modules/transcription/htr_kraken.py` (cliente HTR) · stack homelab `kraken-htr` (microservicio FastAPI sobre kraken) · stack homelab eScriptorium (corrección + entrenamiento de modelos).
