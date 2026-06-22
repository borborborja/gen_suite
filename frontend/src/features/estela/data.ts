// Sample data ported from Estela.dc.html. This stands in for the linkage/discovery
// backend (plan M1) until those endpoints exist; views read from here so the data
// source can later be swapped for real API calls without touching the UI.

export type Conf = "confirmed" | "inferred";

export interface Person {
  name: string;
  as?: string;
  initial: string;
  years: string;
  place: string;
  conf: Conf;
  sources: number;
}

export const people: Record<string, Person> = {
  joan: { name: "Joan Vidal Soler", as: "Joannes", initial: "JV", years: "1750 – 1812", place: "Vallbona de les Monges", conf: "confirmed", sources: 5 },
  francesc: { name: "Francesc Vidal Roca", initial: "FV", years: "1718 – 1779", place: "Vallbona", conf: "confirmed", sources: 3 },
  maria: { name: "María Soler Camps", initial: "MS", years: "1722 – 1788", place: "Vallbona", conf: "inferred", sources: 1 },
  isabel: { name: "Isabel Camps Pujol", initial: "IC", years: "1753 – 1820", place: "Vallbona", conf: "confirmed", sources: 2 },
  pere: { name: "Pere Vidal", initial: "PV", years: "~1690 – 1751", place: "Vallbona", conf: "inferred", sources: 0 },
  anna: { name: "Anna Roca", initial: "AR", years: "~1694 – 1760", place: "Vallbona", conf: "inferred", sources: 0 },
  jaume: { name: "Jaume Soler", initial: "JS", years: "~1695 – 1759", place: "L'Espluga", conf: "inferred", sources: 0 },
  caterina: { name: "Caterina Camps", initial: "CC", years: "~1698 – 1761", place: "L'Espluga", conf: "inferred", sources: 0 },
  francescj: { name: "Francesc Vidal Camps", initial: "FV", years: "1775 – 1841", place: "Vallbona", conf: "confirmed", sources: 2 },
  mariaj: { name: "María Vidal Camps", initial: "MV", years: "1778 – 1855", place: "Vallbona", conf: "confirmed", sources: 1 },
  perej: { name: "Pere Vidal Camps", initial: "PV", years: "1781 – 1849", place: "Vallbona", conf: "inferred", sources: 0 },
  teresaj: { name: "Teresa Vidal Camps", initial: "TV", years: "1784 – 1862", place: "Vallbona", conf: "inferred", sources: 1 },
};

export type ColorKey = "fg" | "ok" | "muted" | "warn";

export interface TreeField { k: string; v: string; c: ColorKey }
export interface Mention { role: string; name: string }
export interface Evidence { cat: string; text: string; s: string; c: ColorKey }
export interface Relative { id: string; name: string; rel: string; mentionId?: string }
export type ConfLevel = "ALTA" | "MEDIA" | "BAJA";

export interface Discovery {
  id: string;
  person: string;
  candidateId?: string; // present when loaded from the live linkage API
  cLevel: ConfLevel;
  pct: number;
  recTitle: string;
  recRef: string;
  folio: string;
  recQuote: string;
  docId?: string; // real source scan (live candidates)
  pageNo?: number;
  treeName: string;
  treeMeta: string;
  treeInitial: string;
  treeFields: TreeField[];
  mentions: Mention[];
  evidence: Evidence[];
  yes: string;
  relatives: Relative[];
  relation?: string; // "self" (default) | "sibling" — kind of discovery
}

export const discoveries: Discovery[] = [
  {
    id: "d1", person: "joan", cLevel: "ALTA", pct: 92, recTitle: "Bautismo · 1750 · Vallbona", recRef: "BAPT. VALLBONA · f.34v", folio: "f. 34 v",
    recQuote: "Joannes, fill de Francesc Vidal y de Maria Soler, fou batejat als XVIII de mars de MDCCL…",
    treeName: "Joan Vidal Soler", treeMeta: "n. ~1750 · Vallbona", treeInitial: "JV",
    treeFields: [{ k: "Nacimiento", v: "~1750", c: "fg" }, { k: "Lugar", v: "Vallbona", c: "fg" }, { k: "Padre", v: "Francesc Vidal", c: "ok" }, { k: "Madre", v: "— sin dato", c: "muted" }],
    mentions: [{ role: "Bautizado", name: "Joannes (Joan)" }, { role: "Padre", name: "Francesc Vidal" }, { role: "Madre", name: "Maria Soler" }, { role: "Padrinos", name: "Jaume Soler · Caterina Camps" }],
    evidence: [
      { cat: "FAMILIA", text: "El padre «Francesc Vidal» coincide con el padre de tu árbol", s: "Fuerte", c: "ok" },
      { cat: "NOMBRE", text: "Joannes ≈ Joan (forma latina del mismo nombre)", s: "Fuerte", c: "ok" },
      { cat: "FECHA", text: "1750 encaja con su nacimiento estimado", s: "Media", c: "warn" },
      { cat: "LUGAR", text: "Vallbona — misma parroquia", s: "Fuerte", c: "ok" },
    ],
    yes: "Sí, es Joan Vidal",
    relatives: [
      { id: "maria", name: "María Soler", rel: "Madre · sugerida" },
      { id: "jaume", name: "Jaume Soler", rel: "Padrino · sugerido" },
      { id: "caterina", name: "Caterina Camps", rel: "Madrina · sugerida" },
    ],
  },
  {
    id: "d3", person: "francesc", cLevel: "ALTA", pct: 88, recTitle: "Defunción · 1779 · Vallbona", recRef: "ÒBITS VALLBONA · f.12r", folio: "f. 12 r",
    recQuote: "Als 4 de juny morí Francesc Vidal, pagès, de edat de LXI anys…",
    treeName: "Francesc Vidal Roca", treeMeta: "1718 – ? · Vallbona", treeInitial: "FV",
    treeFields: [{ k: "Nacimiento", v: "1718", c: "fg" }, { k: "Defunción", v: "— sin dato", c: "muted" }, { k: "Oficio", v: "Pagès", c: "fg" }, { k: "Lugar", v: "Vallbona", c: "fg" }],
    mentions: [{ role: "Difunto", name: "Francesc Vidal" }, { role: "Edad", name: "61 años" }, { role: "Oficio", name: "Pagès" }],
    evidence: [
      { cat: "EDAD", text: "61 años en 1779 → nacido ~1718, encaja exacto", s: "Fuerte", c: "ok" },
      { cat: "NOMBRE", text: "Francesc Vidal — coincidencia directa", s: "Fuerte", c: "ok" },
      { cat: "LUGAR", text: "Vallbona — misma parroquia", s: "Media", c: "warn" },
    ],
    yes: "Sí, es Francesc", relatives: [],
  },
  {
    id: "d4", person: "isabel", cLevel: "ALTA", pct: 81, recTitle: "Bautismo · 1753 · Vallbona", recRef: "BAPT. VALLBONA · f.51r", folio: "f. 51 r",
    recQuote: "Isabel, filla de Pau Camps y de Margarida Pujol, batejada…",
    treeName: "Isabel Camps Pujol", treeMeta: "~1753 · Vallbona", treeInitial: "IC",
    treeFields: [{ k: "Nacimiento", v: "~1753", c: "fg" }, { k: "Padre", v: "Pau Camps", c: "ok" }, { k: "Madre", v: "Margarida Pujol", c: "ok" }, { k: "Cónyuge", v: "Joan Vidal", c: "fg" }],
    mentions: [{ role: "Bautizada", name: "Isabel" }, { role: "Padre", name: "Pau Camps" }, { role: "Madre", name: "Margarida Pujol" }],
    evidence: [
      { cat: "FAMILIA", text: "Padres «Pau Camps» y «Margarida Pujol» coinciden", s: "Fuerte", c: "ok" },
      { cat: "NOMBRE", text: "Isabel — coincidencia directa", s: "Media", c: "warn" },
      { cat: "FECHA", text: "1753 encaja con su nacimiento", s: "Media", c: "warn" },
    ],
    yes: "Sí, es Isabel", relatives: [],
  },
  {
    id: "d2", person: "maria", cLevel: "MEDIA", pct: 74, recTitle: "Matrimonio · 1745 · Vallbona", recRef: "MATR. VALLBONA · f.8v", folio: "f. 8 v",
    recQuote: "…contragueren matrimoni Francesc Vidal y Maria Soler, donzella…",
    treeName: "María Soler Camps", treeMeta: "~1722 · Vallbona", treeInitial: "MS",
    treeFields: [{ k: "Nacimiento", v: "~1722", c: "fg" }, { k: "Cónyuge", v: "Francesc Vidal", c: "ok" }, { k: "Lugar", v: "Vallbona", c: "fg" }],
    mentions: [{ role: "Esposo", name: "Francesc Vidal" }, { role: "Esposa", name: "Maria Soler" }],
    evidence: [
      { cat: "FAMILIA", text: "El esposo «Francesc Vidal» está en tu árbol", s: "Fuerte", c: "ok" },
      { cat: "NOMBRE", text: "Maria Soler — coincidencia directa", s: "Media", c: "warn" },
      { cat: "FECHA", text: "1745 — boda 5 años antes de Joan (1750)", s: "Débil", c: "muted" },
    ],
    yes: "Sí, es María", relatives: [],
  },
  {
    id: "d6", person: "teresaj", cLevel: "MEDIA", pct: 67, recTitle: "Confirmación · 1796 · Vallbona", recRef: "CONF. VALLBONA · f.3r", folio: "f. 3 r",
    recQuote: "…rebé la confirmació Teresa Vidal, filla de Joan Vidal…",
    treeName: "Teresa Vidal Camps", treeMeta: "~1784 · Vallbona", treeInitial: "TV",
    treeFields: [{ k: "Nacimiento", v: "~1784", c: "fg" }, { k: "Padre", v: "Joan Vidal", c: "ok" }, { k: "Lugar", v: "Vallbona", c: "fg" }],
    mentions: [{ role: "Confirmada", name: "Teresa Vidal" }, { role: "Padre", name: "Joan Vidal" }],
    evidence: [
      { cat: "FAMILIA", text: "El padre «Joan Vidal» coincide con tu árbol", s: "Fuerte", c: "ok" },
      { cat: "EDAD", text: "12 años en 1796 → encaja con 1784", s: "Media", c: "warn" },
    ],
    yes: "Sí, es Teresa", relatives: [],
  },
  {
    id: "d5", person: "pere", cLevel: "BAJA", pct: 49, recTitle: "Padrón · 1787 · Vallbona", recRef: "PADRÓ VALLBONA · f.22", folio: "f. 22",
    recQuote: "…casa de Pere Vidal, ab muller y tres fills…",
    treeName: "Pere Vidal", treeMeta: "~1690 · Vallbona", treeInitial: "PV",
    treeFields: [{ k: "Nacimiento", v: "~1690", c: "fg" }, { k: "Lugar", v: "Vallbona", c: "fg" }, { k: "Nota", v: "2 candidatos posibles", c: "warn" }],
    mentions: [{ role: "Cabeza", name: "Pere Vidal" }, { role: "Familia", name: "muller + 3 fills" }],
    evidence: [
      { cat: "NOMBRE", text: "Pere Vidal — pero hay 2 personas con ese nombre", s: "Débil", c: "muted" },
      { cat: "FECHA", text: "1787 — tu Pere habría muerto en 1751", s: "Débil", c: "muted" },
    ],
    yes: "Sí, es Pere", relatives: [],
  },
];

// ---- Inicio dashboard ----
export const stats = [
  { label: "Personas", value: "1.482", sub: "540 familias", color: "var(--fg)" },
  { label: "Fuentes", value: "3.940", sub: "actas enlazadas", color: "var(--fg)" },
  { label: "Pendientes", value: "7", sub: "por revisar", color: "var(--accent)" },
  { label: "Confirmados", value: "156", sub: "este mes", color: "var(--ok)" },
];

export const jobs = [
  { title: "Matrimonis de Vallbona", detail: "240 / 600", pct: "40%", stage: "Leyendo la escritura…" },
  { title: "Òbits de Vallbona", detail: "430 / 600", pct: "72%", stage: "Extrayendo actas…" },
];

export const activity = [
  { text: "Añadiste a María Soler como inferida", time: "hace 4 min", color: "var(--warn)" },
  { text: "Confirmaste el bautizo de Joan Vidal (1750)", time: "hace 12 min", color: "var(--ok)" },
  { text: "Libro «Baptismes de Vallbona»: 312/312 leídas", time: "hace 1 h", color: "var(--accent)" },
  { text: "Importaste tu árbol — 1.482 personas", time: "ayer", color: "var(--muted)" },
];

// ---- Person detail ----
export interface LifelineEvent { title: string; date: string; place: string; conf: Conf; source: string }
export interface FamilyLink { id: string; rel: string; conf: Conf }
export interface SourceRef { title: string; ref: string }

export const personDetails: Record<string, { lifeline: LifelineEvent[]; family: FamilyLink[]; sources: SourceRef[] }> = {
  joan: {
    lifeline: [
      { title: "Bautismo", date: "18 mar 1750", place: "Vallbona", conf: "confirmed", source: "Baptismes Vallbona · f.34v" },
      { title: "Matrimonio con Isabel Camps", date: "1774", place: "Vallbona", conf: "inferred", source: "Matrimonis Vallbona · f.40r" },
      { title: "Defunción", date: "1812", place: "Vallbona", conf: "inferred", source: "Òbits Vallbona · f.88v" },
    ],
    family: [
      { id: "francesc", rel: "Padre", conf: "confirmed" }, { id: "maria", rel: "Madre", conf: "inferred" },
      { id: "isabel", rel: "Cónyuge", conf: "confirmed" }, { id: "francescj", rel: "Hijo", conf: "confirmed" },
      { id: "mariaj", rel: "Hija", conf: "confirmed" }, { id: "teresaj", rel: "Hija", conf: "inferred" },
    ],
    sources: [
      { title: "Baptismes de Vallbona 1700–1750", ref: "f.34v · acta 2" },
      { title: "Matrimonis de Vallbona", ref: "f.40r" },
    ],
  },
};

// ---- Biblioteca ----
export interface Book {
  title: string; place: string; years: string; actas: string | number; matches: number;
  ready: boolean; processing: boolean; pct?: string; stage?: string;
  statusLabel: string; statusBg: string;
}

export const books: Book[] = [
  { title: "Baptismes de Vallbona", place: "Vallbona", years: "1700–1750", actas: 312, matches: 28, ready: true, processing: false, statusLabel: "Lista", statusBg: "rgba(47,125,84,.92)" },
  { title: "Matrimonis de Vallbona", place: "Vallbona", years: "1740–1800", actas: "—", matches: 0, ready: false, processing: true, pct: "40%", stage: "Leyendo escritura · 240/600", statusLabel: "Leyendo", statusBg: "rgba(217,83,30,.92)" },
  { title: "Òbits de Vallbona", place: "Vallbona", years: "1750–1820", actas: "—", matches: 0, ready: false, processing: true, pct: "72%", stage: "Extrayendo actas · 430/600", statusLabel: "Extrayendo", statusBg: "rgba(187,125,26,.95)" },
  { title: "Padró de Vallbona", place: "Vallbona", years: "1787", actas: 84, matches: 0, ready: false, processing: false, statusLabel: "Necesita revisión", statusBg: "rgba(187,125,26,.95)" },
  { title: "Quintes i lleves", place: "la Comarca", years: "1808", actas: 46, matches: 0, ready: false, processing: false, statusLabel: "Error en 3 págs.", statusBg: "rgba(192,57,43,.92)" },
];

// ---- Visor ----
export const transcription: { text: string; hl: boolean }[] = [
  { text: "Baptisme de Joannes Vidal", hl: false },
  { text: "Als XVIII dies del mes de mars de l'any MDCCL,", hl: true },
  { text: "jo, lo rector, batejí Joannes, fill legítim de", hl: true },
  { text: "Francesc Vidal, pagès, y de Maria Soler, conjuges.", hl: true },
  { text: "Foren padrins Jaume Soler y Caterina Camps,", hl: false },
  { text: "tots de la present parròquia de Vallbona.", hl: false },
  { text: "", hl: false },
  { text: "Baptisme de Margarida Roig", hl: false },
  { text: "Lo mateix dia batejí Margarida, filla de…", hl: false },
];

export interface VisorActa { id: string; type: string; date: string; place: string; linkLabel: string; linked: boolean; mentions: Mention[] }
export const visorActas: VisorActa[] = [
  { id: "a1", type: "Bautizo", date: "18 mar 1750", place: "Vallbona", linkLabel: "CANDIDATA", linked: true, mentions: [{ role: "Bautizado", name: "Joannes Vidal" }, { role: "Padre", name: "Francesc Vidal" }, { role: "Madre", name: "Maria Soler" }, { role: "Padrinos", name: "Jaume Soler · Caterina Camps" }] },
  { id: "a2", type: "Bautizo", date: "18 mar 1750", place: "Vallbona", linkLabel: "SIN ENLAZAR", linked: false, mentions: [{ role: "Bautizada", name: "Margarida Roig" }, { role: "Padre", name: "Pau Roig" }, { role: "Madre", name: "Elena Mas" }] },
];

// ---- Buscar ----
export const searchResults = [
  { title: "Baptismes de Vallbona", tagLabel: "BAUTIZO", snippet: "…Joannes, fill de Francesc Vidal y de Maria Soler…", ref: "f.34v · 1750 · coincide con tu árbol" },
  { title: "Matrimonis de Vallbona", tagLabel: "MATRIMONIO", snippet: "…contragueren matrimoni Francesc Vidal y Maria Soler…", ref: "f.8v · 1745" },
  { title: "Òbits de Vallbona", tagLabel: "DEFUNCIÓN", snippet: "…morí Francesc Vidal, pagès, de edat de LXI anys…", ref: "f.12r · 1779" },
];

// ---- Ajustes ----
export const engines = [
  { name: "Estela — automático", desc: "Equilibrio entre calidad y coste. Cambia de motor según la dificultad de la página.", cost: "incluido", on: true },
  { name: "Kraken (local)", desc: "Se ejecuta en tu equipo. Gratis y privado, algo más lento.", cost: "gratis", on: false },
  { name: "Lectura de alta calidad (nube)", desc: "Máxima precisión para letra difícil. Coste por página.", cost: "~0,01 €/pág", on: false },
];

export const shortcuts = [
  { key: "S", label: "Sí, es la persona" },
  { key: "N", label: "No es la persona" },
  { key: "␣", label: "No lo sé" },
  { key: "Z", label: "Ampliar manuscrito" },
];
