# Brief de producto y diseño — Descubridor de parientes en archivos históricos

> Documento para la fase de **diseño de interfaz**. Describe **qué debe ser el producto** y **cómo debe sentirse** para resultar funcional e intuitivo. No es documentación técnica: traduce la arquitectura a experiencia de usuario. Idioma del producto: **español** (preparado para catalán e inglés).

---

## 1. En una frase

Una aplicación que **lee miles de páginas de libros históricos** (bautizos, matrimonios y defunciones, s. XVII–XIX), **extrae quién aparece en cada acta** y, partiendo del **árbol genealógico del usuario**, le **propone parientes y registros con evidencia**, para que la persona **confirme con un clic** y su árbol crezca **sin riesgo de corromperse**.

El valor no es "buscar en un archivo": es que **el archivo venga a ti**, ordenado alrededor de tu familia, con la prueba delante (la imagen del manuscrito) y la decisión siempre en tus manos.

---

## 2. Escenario norte (la historia que el diseño debe hacer posible)

> María tiene su árbol con ~1.500 personas. Una mañana abre la app y ve: **"7 posibles parientes nuevos"**. Entra. La primera tarjeta enfrenta a su tatarabuelo **Juan Pérez** (lo que ella ya sabe) con un **acta de bautizo de 1750**: a la derecha, la **foto del manuscrito** con la letra original; debajo, en lenguaje claro, **por qué** la app cree que es él: *"el padre 'Domingo Pérez' coincide con el padre de tu árbol"*, *"Joannes ≈ Juan"*, *"1750 encaja con su nacimiento"*. María amplía la imagen, lo lee, y pulsa **"Sí, es Juan"**. Al instante la app le dice: *"Esta acta también nombra a una madre, **Isabel López**, y dos padrinos. ¿Los añado a tu árbol?"*. María acepta a la madre. Aparece una **nueva persona** marcada como *inferida*, con un enlace a la imagen que lo prueba. Y abajo: **"Hemos encontrado 3 actas más donde aparece Isabel"**. El descubrimiento continúa.

Si el diseño consigue que ese flujo se sienta **claro, fiable y adictivo**, el producto funciona.

---

## 3. Para quién es

| Perfil | Qué quiere | Implicación de diseño |
|---|---|---|
| **Genealogista aficionado** (perfil principal) | Hacer crecer su árbol con pruebas reales, sin saber de bases de datos ni paleografía | Lenguaje llano, cero jerga técnica, la prueba siempre visible, decisiones binarias sencillas |
| **Investigador/a metódico/a** | Rigor: cada dato con su fuente, distinguir lo confirmado de lo inferido, revisar en lote | Procedencia exhaustiva, filtros y orden, revisión por teclado, exportación |
| **Recién llegado/a** | Empezar sin fricción: subir su árbol y "ver magia" pronto | Onboarding guiado, estados vacíos que enseñan, primer descubrimiento rápido |

No asumir conocimientos de paleografía, latín ni informática. El usuario piensa en **personas, familias, libros y actas** — nunca en "pipelines", "embeddings" ni "linkage".

---

## 4. Principios de diseño (las estrellas polares)

1. **La prueba, delante.** Toda afirmación se puede abrir hasta la **imagen del manuscrito**. La caligrafía histórica es el contenido héroe, no un adorno.
2. **La persona decide; la app propone.** Nada se fusiona solo. Cada cambio en el árbol nace de un **clic de confirmación** y se puede **deshacer**.
3. **Confianza legible.** La "seguridad" de cada coincidencia se muestra siempre, en lenguaje humano y con su **porqué** desglosado. Nunca una caja negra.
4. **Inferido ≠ confirmado.** Lo que aporta la app y lo que el usuario ha validado se distinguen **a simple vista** (color, etiqueta), siempre.
5. **El árbol es la lente.** El corpus es gigantesco (cientos de miles de páginas); el usuario no lo navega en lineal: **llega a él a través de su familia** y de la búsqueda.
6. **Lenguaje claro.** "Posibles parientes", "coincidencias", "actas", "fuentes" — nunca términos de ingeniería.
7. **La recompensa del descubrimiento.** Encontrar un ancestro es emocionante y, a veces, íntimo. El tono cuida ese momento: celebra sin frivolizar.
8. **Rápido a escala.** Revisar puede implicar decenas de tarjetas: triaje por teclado, en lote, con sesiones cómodas.

---

## 5. Conceptos clave (glosario para quien diseña)

- **Árbol / Persona / Familia** — el árbol del usuario. Una persona tiene nombres, sexo, eventos (nacimiento, defunción…) y relaciones (padres, cónyuge, hijos).
- **Libro / Documento / Página** — un registro histórico digitalizado (p. ej. "Bautismos de Villanueva 1700–1750"), compuesto de páginas-imagen.
- **Transcripción** — el texto de una página, obtenido por **lectura automática de la escritura (HTR)**. Puede tener erratas; es corregible.
- **Acta / Registro** — un asiento concreto dentro de una página (un bautizo, un matrimonio, una defunción). Una página puede contener **varias actas**.
- **Mención** — cada persona nombrada dentro de un acta, **con su papel**: bautizado/a, padre, madre, padrino, madrina, cónyuge, testigo…
- **Coincidencia / Candidato** — una hipótesis de que **una mención del archivo = una persona de tu árbol**, con una **puntuación de confianza** y su **evidencia**.
- **Descubrimiento** — una coincidencia o un pariente nuevo propuesto, pendiente de tu revisión.
- **Fuente / Cita** — el vínculo entre un dato de tu árbol y **la prueba que lo respalda** (acta + imagen de la página).
- **Confianza** — cuán segura está la app de una coincidencia (alta / media / baja, con porcentaje y desglose).
- **Inferido vs confirmado** — *inferido* = propuesto por la app (o aún sin validar); *confirmado* = validado por ti.

---

## 6. Arquitectura de información y navegación

Navegación principal (5 áreas) + ajustes:

```
●  Inicio            (panel: estado, "X posibles parientes nuevos", actividad)
●  Mi árbol          (buscar/explorar personas · ficha de persona · importar GEDCOM)
●  Descubrimientos   ★ corazón del producto: cola de revisión de coincidencias y parientes nuevos
●  Biblioteca        (libros: importar/subir · estado de procesado · visor de página)
●  Buscar            (en el archivo y en el árbol; por texto y por significado)
⚙  Ajustes           (motores de IA/HTR, cuenta, idioma, exportar)
```

Modelo mental: **Inicio** es el pulso del proyecto; **Descubrimientos** es donde se hace el trabajo gratificante; **Mi árbol** y **Biblioteca** son los dos "almacenes" (lo tuyo y el archivo) que **Descubrimientos** conecta; **Buscar** es la entrada transversal.

Recomendado: **escritorio primero** (revisión densa, imagen grande, atajos de teclado); **móvil** como compañero para revisar descubrimientos sueltos y consultar el árbol.

---

## 7. Flujos principales

### F1 · Onboarding (primer uso)
1. **Crea/entra** → pantalla de bienvenida con dos caminos claros: **"Importa tu árbol (GEDCOM)"** o **"Empieza a mano"**.
2. Tras importar: resumen ("1.482 personas, 540 familias") y un siguiente paso obvio: **"Añade tu primer libro"** (subir imágenes/PDF o importar de FamilySearch).
3. El libro se **procesa** (lectura + extracción) con progreso visible; mientras, se explica qué está pasando en lenguaje llano.
4. Al terminar: **"Buscando parientes en tu árbol…"** → primeras tarjetas en **Descubrimientos**. Meta: **primer descubrimiento cuanto antes**.

### F2 · Descubrir parientes (núcleo, el bucle adictivo)
Persona del árbol → la app propone **coincidencias ordenadas por confianza** → abres una → ves **imagen + datos + porqué** → **Confirmar / Rechazar / No lo sé** → al confirmar, la app ofrece **los demás nombres del acta como parientes nuevos** → aceptas los que correspondan → cada nuevo pariente **se convierte en semilla** y genera más descubrimientos. (El "volante" de la familia desplegándose.)

### F3 · Explorar un libro
Biblioteca → un libro → **visor de página**: imagen a un lado, **transcripción** al otro, y las **actas extraídas** listadas; al pasar el cursor por un acta se **resalta su zona en la imagen**. Puedes **corregir** la transcripción (mejora futuras lecturas) y ver **qué actas ya están enlazadas a tu árbol**.

### F4 · Ficha de persona
Mi árbol → una persona → **línea de vida** (eventos en orden, cada uno con su **fuente** clicable a la imagen), **familia** (padres/cónyuge/hijos, con lo inferido marcado) y la acción destacada **"Buscar registros de esta persona"**.

---

## 8. Pantallas en detalle

Para cada pantalla: **Propósito · Contenido · Acciones · Estados**.

### 8.1 · Inicio (panel)
- **Propósito:** dar el pulso del proyecto y llevar al trabajo de hoy.
- **Contenido:** titular grande **"X posibles parientes nuevos"** (CTA a Descubrimientos); tarjetas de estado (personas en el árbol · libros y páginas procesadas · descubrimientos pendientes/confirmados); **actividad reciente** ("Añadiste a Isabel López", "Libro Villanueva: 600/600 leídas"); procesos en curso con progreso.
- **Acciones:** *Revisar descubrimientos*, *Añadir libro*, *Importar/abrir árbol*.
- **Estados:** **vacío** (sin árbol → "Importa tu árbol"; con árbol y sin libros → "Añade tu primer libro"); **procesando** (barras de progreso); **al día** ("Nada pendiente ✔ — añade más libros para descubrir más").

### 8.2 · Mi árbol — explorar
- **Propósito:** encontrar y navegar personas.
- **Contenido:** buscador por nombre/apellido; lista/cuadrícula de personas (nombre, años de vida, nº de fuentes, chip si tiene descubrimientos pendientes); acceso a **vista de árbol** (genograma) y filtros (apellido, lugar, época, "con/sin pruebas", "tiene descubrimientos").
- **Acciones:** abrir ficha, *Buscar registros*, importar GEDCOM, exportar.
- **Estados:** vacío (importar/crear), búsqueda sin resultados (sugerir variantes ortográficas).

### 8.3 · Ficha de persona
- **Propósito:** todo lo que sabemos de una persona y su prueba.
- **Contenido:**
  - **Cabecera:** nombre principal (y **"nombre como aparece"** si difiere, p. ej. *Joannes*), sexo, fechas, lugares; **chip global** si hay datos inferidos sin revisar.
  - **Línea de vida:** eventos en orden (bautizo, matrimonio, defunción…), cada uno con **fecha, lugar y una miniatura de la fuente** clicable a la imagen; lo **inferido** marcado.
  - **Familia:** padres, cónyuge(s), hijos; cada relación muestra si es **confirmada o inferida**; añadir/editar manual.
  - **Fuentes:** lista de actas que respaldan a esta persona.
  - **Acción protagonista:** **"Buscar registros de esta persona"** (lanza F2 para esta persona).
- **Acciones:** confirmar/quitar inferencias, editar, ver en árbol, exportar ficha.
- **Estados:** sin fuentes ("Aún sin pruebas — busca registros"); buscando (progreso); con descubrimientos pendientes (aviso destacado).

### 8.4 · Biblioteca (libros)
- **Propósito:** gestionar el archivo y su procesado.
- **Contenido:** rejilla/lista de libros (portada/primera página, título, lugar y época, **estado**: *Leyendo 240/600 · Extrayendo · Lista · Necesita revisión*, y **nº de actas** y **coincidencias con tu árbol**); botón **Añadir libro** (subir imágenes/PDF o **importar de FamilySearch**).
- **Acciones:** abrir visor, reanudar/relanzar procesado, editar metadatos (lugar, años), publicar/compartir, borrar.
- **Estados:** vacío ("Añade tu primer libro"); procesando (progreso por etapas: *leyendo escritura → extrayendo actas → buscando parientes*); error en páginas concretas (marcadas, reintentables).

### 8.5 · Visor de página ★ (imagen + transcripción + actas)
- **Propósito:** conectar **imagen ↔ texto ↔ actas** y permitir corrección.
- **Contenido (tres zonas):**
  1. **Imagen** (héroe): zoom, desplazamiento, rotar; **resalta la zona** del acta o línea activa.
  2. **Transcripción**: texto de la página, **editable** ("Corregir"); resalta la línea bajo el cursor en sincronía con la imagen.
  3. **Actas de esta página**: tarjetas (tipo, fecha, lugar) con sus **menciones por papel** (bautizado, padre, madre…); indicador de **enlace al árbol** (enlazada / candidata / sin enlazar).
- **Acciones:** corregir transcripción (mejora lecturas futuras), confirmar/crear acta, **enlazar una mención a una persona** del árbol, navegar páginas, descargar imagen.
- **Estados:** sin transcribir aún ("Leer esta página"); baja confianza de lectura (aviso + sugerir relectura con motor de mayor calidad); en corrección (guardado con feedback).

### 8.6 · Descubrimientos ★★ (la cola de revisión — el corazón)
- **Propósito:** revisar y decidir coincidencias y parientes nuevos, rápido y con confianza.
- **Estructura:**
  - **Encabezado de triaje:** total pendiente; **orden** (confianza ↓ por defecto) y **filtros** (persona, libro, tipo de acta, nivel de confianza); modo **"sesión de revisión"** (una tarjeta a la vez, navegable por teclado).
  - **Tarjeta de coincidencia (comparación):**
    ```
    ┌─ TU ÁRBOL ───────────┐   ┌─ POSIBLE COINCIDENCIA ───────────────────────┐
    │  Juan Pérez           │   │  Bautizo · 1750 · Villanueva                  │
    │  n. ~1750 · Villanueva│   │  ┌───────────────────────────┐                │
    │  Padre: Domingo Pérez │   │  │   [IMAGEN DEL MANUSCRITO]  │  (ampliar)     │
    │  Madre: —             │   │  └───────────────────────────┘                │
    │                       │   │  "Joannes, hijo de Domingo Pérez y de…"        │
    │                       │   │  Bautizado: Joannes · Padre: Domingo Pérez …  │
    └───────────────────────┘   └───────────────────────────────────────────────┘
       Confianza: ALTA · 92%
       ▸ 👪 Familia   El padre "Domingo Pérez" coincide con el de tu árbol   ✔ fuerte
       ▸ 🔤 Nombre    Joannes ≈ Juan                                         ✔
       ▸ 📅 Fecha     1750 encaja con su nacimiento                          ✔
       ▸ 📍 Lugar     Villanueva — misma parroquia                          ✔
       [ Sí, es Juan Pérez ]   [ No es él ]   [ No lo sé ]
    ```
  - **Evidencia:** un indicador global (alta/media/baja + %) **y** las señales desglosadas en lenguaje humano, con la **familia** como señal más fuerte y visible. Nunca solo un número.
  - **Tras "Sí":** panel **"Esta acta también menciona…"** con las demás personas (madre, padrinos, testigos) y su **relación sugerida**; cada una con **[Añadir]** (se creará como **inferida**, con su fuente) o **[Ya está en mi árbol]**; opción **[Añadir todos]**. Luego, gancho: **"Hemos encontrado N actas más con Isabel López"**.
- **Acciones y atajos:** Confirmar (S), Rechazar (N), Saltar/No lo sé (espacio), ampliar imagen (Z), deshacer (Ctrl/Cmd+Z). Toda decisión es **reversible**.
- **Estados:** vacío feliz ("Nada que revisar ✔ — añade más libros"); cargando candidatos; sin imagen disponible (mostrar texto y avisar); ambiguo (varias personas del árbol podrían encajar → elegir cuál).

### 8.7 · Buscar
- **Propósito:** entrada transversal al archivo y al árbol.
- **Contenido:** una caja; resultados en dos pestañas (**Archivo** / **Mi árbol**); en Archivo, cada resultado muestra **fragmento + miniatura de la página** y acceso al visor; la búsqueda entiende **variantes** (acentos, latín/vernáculo, ortografía) y permite **"buscar por significado"** además de por palabra exacta.
- **Acciones:** abrir visor/ficha, **enlazar** un resultado a una persona, guardar búsqueda.
- **Estados:** vacío con ejemplos; sin resultados (sugerir variantes); muchos resultados (facetas por lugar/época/tipo).

### 8.8 · Ajustes (resumen — menor peso visual)
- **Motores de IA / lectura:** elegir proveedor para **leer escritura** (Kraken local / nube) y para **extraer datos**, con un **modo recomendado** por defecto; gestión de claves; aviso de **coste estimado** al procesar lotes grandes.
- **Cuenta, idioma (ES/CA/EN), exportar (GEDCOM), privacidad/compartir.**
- El usuario corriente **no debería necesitar tocar esto** para empezar: que haya un preajuste sensato.

---

## 9. Componentes y patrones transversales

- **Etiqueta Confianza:** alta / media / baja con color + % + tooltip que abre el desglose. Coherente en todo el producto.
- **Chip Inferido / Confirmado:** distinción visual constante (p. ej. inferido = contorno punteado/acento ámbar; confirmado = sólido/verde). Acompañar de texto, nunca solo color.
- **Barra/lista de evidencia:** señales con icono + frase humana + fuerza (fuerte/media/débil). Reutilizable en tarjeta y ficha.
- **Visor de imagen:** zoom/pan/rotar, pantalla completa, resaltado de región/línea, descarga. Es el componente más usado: que sea excelente.
- **Procedencia ("ver fuente"):** desde cualquier dato → miniatura → imagen completa con la zona marcada. Un patrón único, omnipresente.
- **Indicadores de proceso:** los trabajos largos (leer, extraer, buscar parientes) muestran **etapa + progreso (n/total) en tiempo real** y son **cancelables/reanudables**; explican en llano qué hacen.
- **Deshacer / Toasts:** cada acción que cambia el árbol confirma con un toast y **"Deshacer"**.
- **Estados vacíos didácticos:** cada vacío enseña el siguiente paso con una sola acción clara.

---

## 10. Estados, vacíos y errores (patrón global)

- **Vacío:** ilustración sobria + una frase + **una** acción primaria.
- **Cargando:** esqueletos; para procesos largos, **progreso real** (no spinners infinitos).
- **Procesando (asíncrono):** banner persistente con etapa y n/total; el usuario puede seguir trabajando.
- **Error:** mensaje humano + causa probable + acción (reintentar / reintentar solo las páginas fallidas / ajustar motor). Nunca un código a secas.
- **Baja confianza:** se señala, no se oculta; se ofrece mejorar (relectura con mejor motor, corrección manual).

---

## 11. Tono y microcopy

- **Voz:** cercana, clara, respetuosa; experta sin ser técnica; celebra los hallazgos sin teatralizar temas sensibles (defunciones, mortalidad infantil frecuente en estos libros).
- **Usar:** "posibles parientes", "coincidencia", "acta", "fuente", "leer la escritura", "lo confirmas tú".
- **Evitar:** "entity resolution", "linkage", "embeddings", "OCR/HTR" (di "lectura de la escritura"), "pipeline", "score".
- **Ejemplos:**
  - Botón: **"Sí, es Juan Pérez"** / **"No es él"** / **"No lo sé"**.
  - Evidencia: *"El padre que aparece en el acta coincide con el padre de tu árbol."*
  - Tras confirmar: *"Añadido a tu árbol como inferido, con su fuente. Puedes deshacerlo."*
  - Vacío: *"Aún no hay nada que revisar. Añade un libro y buscaremos parientes por ti."*
  - Coste: *"Procesar este libro (600 páginas) tiene un coste estimado de … ¿Continuar?"*

---

## 12. Accesibilidad e idioma

- **Idioma:** español por defecto; estructura preparada para **catalán e inglés** (textos externalizables).
- **Contraste y color:** AA mínimo; **no** comunicar estados solo por color (siempre icono/etiqueta).
- **Teclado:** la revisión de Descubrimientos es 100% operable por teclado (atajos visibles).
- **Imágenes:** el manuscrito es contenido; ofrecer la **transcripción como texto** asociado (lectura por lectores de pantalla y por buscadores).
- **Carga cognitiva:** una decisión principal por pantalla; jerarquía visual fuerte.

---

## 13. Responsive y plataformas

- **Escritorio (principal):** dos paneles para comparar; imagen grande; revisión por teclado; tablas/filtros densos.
- **Tableta:** visor cómodo, revisión a una tarjeta.
- **Móvil:** consultar el árbol y **revisar descubrimientos sueltos** (tarjeta a pantalla completa, gestos para imagen, botones grandes Sí/No/No-sé). No es el lugar del trabajo masivo, pero sí del "vistazo diario".

---

## 14. Dirección visual (sugerida, no cerrada)

- **Mood:** **archivo histórico + interfaz moderna y limpia.** Calidez de papel/pergamino y tinta como acentos; lienzo claro, mucho aire, tipografía legible; **la caligrafía antigua es la protagonista** y todo lo demás la enmarca con sobriedad.
- **Color:** base neutra cálida; **un acento de confianza** (p. ej. verde) para *confirmado*, **un acento de atención** (p. ej. ámbar) para *inferido/pendiente*, rojo reservado a destructivo. Semáforo de confianza coherente.
- **Sensación:** fiable, cuidado, "de archivo serio" pero acogedor; ni frío-corporativo ni recargado-vintage.
- **Continuidad:** si se desea, alinear con el lenguaje visual existente de gen_suite / la identidad "Folio" del usuario para coherencia de marca.

---

## 15. Qué significa "funcional e intuitiva" aquí (métricas de éxito de la experiencia)

- **Tiempo hasta el primer descubrimiento** corto tras subir un libro.
- **% de coincidencias decididas sin ayuda** (la evidencia basta para decidir).
- **Velocidad de revisión** en sesión (tarjetas/min con teclado) sin sensación de fatiga.
- **Cero fusiones accidentales** percibidas; confianza alta en que "nada se rompe".
- **Trazabilidad:** desde cualquier dato, llegar a la imagen-fuente en **1 clic**.

---

## 16. Fuera de alcance del diseño inicial (notas)

- Administración avanzada de motores de IA y costes (basta un preajuste + ajuste simple).
- Entrenamiento de modelos de lectura (el bucle de corrección sí se diseña; la gestión de modelos, no aún).
- Multiusuario/colaboración y publicación pública del archivo (existe el concepto, pero no es el foco del primer diseño).
- Detalles de despliegue/infraestructura.

---

### Resumen para quien diseña
Diseña un producto donde **el árbol del usuario es la lente** sobre un archivo enorme; donde **cada propuesta llega con su prueba (la imagen) y su porqué (evidencia legible)**; donde **la persona confirma con un clic** y el árbol crece marcando siempre **lo inferido frente a lo confirmado**; y donde **descubrir un pariente** se siente claro, fiable y gratificante. La pantalla a la que dedicar más cariño es **Descubrimientos** (§8.6) y el componente más crítico es el **visor de imagen con procedencia** (§9).
