// Shared Spanish label dictionaries — the single source of truth for how job types, record
// types, mention roles and job statuses are shown across every view. (These were previously
// duplicated per-view and had drifted: the same role could read differently per screen.)

export const RECORD_TYPE_LABEL: Record<string, string> = {
  baptism: "Bautismo", marriage: "Matrimonio", death: "Defunción",
  confirmation: "Confirmación", census: "Padrón", will: "Testamento",
  military: "Quintas", other: "Acta",
};

// Generic role names. "principal" is resolved per record type via roleLabel().
export const ROLE_LABEL: Record<string, string> = {
  principal: "Principal", head: "Cabeza de familia", testator: "Testador",
  defendant: "Procesado", soldier: "Soldado",
  father: "Padre", mother: "Madre", spouse: "Cónyuge",
  godfather: "Padrino", godmother: "Madrina",
  son: "Hijo", daughter: "Hija", child: "Hijo/a", sibling: "Hermano/a",
  grandparent: "Abuelo/a", grandchild: "Nieto/a",
  spouse_father: "Suegro", spouse_mother: "Suegra",
  witness: "Testigo", declarant: "Declarante", officiant: "Oficiante",
  heir: "Heredero/a", executor: "Albacea", notary: "Notario",
  in_law: "Pariente político", servant: "Sirviente", lodger: "Huésped",
  relative: "Pariente", other: "Otro",
};

// What "principal" means depends on the act: the baptised in a baptism, the deceased in a
// death record…
const PRINCIPAL_BY_RECORD_TYPE: Record<string, string> = {
  baptism: "Bautizado/a", death: "Difunto/a", marriage: "Contrayente",
  confirmation: "Confirmado/a", census: "Titular", will: "Testador",
};

export function roleLabel(role: string, recordType?: string | null): string {
  if (role === "principal" && recordType && PRINCIPAL_BY_RECORD_TYPE[recordType]) {
    return PRINCIPAL_BY_RECORD_TYPE[recordType];
  }
  return ROLE_LABEL[role] ?? role;
}

// Job type → noun (lists, history) and → gerund (progress cards).
export const JOB_LABEL: Record<string, string> = {
  transcription: "Transcripción", extraction: "Extracción",
  embed_mentions: "Embeddings de menciones", embedding: "Embeddings", embed_document: "Embeddings",
  reembed_corpus: "Re-embeber corpus", linkage: "Descubrimiento", linkage_family: "Descubrimiento familiar",
  reconstruction: "Reconstrucción", rasterize: "Rasterizado de PDF", rerasterize: "Re-rasterizado",
  compact_pdf: "Compactar PDF", index: "Índice", fs_download: "Descarga de FamilySearch",
};
export const JOB_GERUND: Record<string, string> = {
  transcription: "Transcribiendo", extraction: "Extrayendo",
  embed_mentions: "Generando embeddings", embedding: "Generando embeddings", embed_document: "Generando embeddings",
  reembed_corpus: "Re-embebiendo corpus", linkage: "Buscando coincidencias", linkage_family: "Buscando hermanos",
  reconstruction: "Reconstruyendo árbol", rasterize: "Rasterizando", rerasterize: "Re-rasterizando",
  compact_pdf: "Compactando PDF", index: "Parseando índice", fs_download: "Descargando",
};
export const jobLabel = (t: string) => JOB_LABEL[t] ?? t;
export const jobGerund = (t: string) => JOB_GERUND[t] ?? jobLabel(t);

export const STATUS_COLOR: Record<string, string> = {
  completed: "var(--ok)", running: "var(--accent)", queued: "var(--warn)",
  error: "var(--danger)", cancelled: "var(--muted)",
};

export const STATUS_LABEL: Record<string, string> = {
  completed: "completado", running: "en curso", queued: "en cola",
  error: "error", cancelled: "cancelado",
};
export const statusLabel = (s: string) => STATUS_LABEL[s] ?? s;
