// Maps the live linkage API's CandidateOut onto the Estela Discovery view-model, so the
// DiscoveryView renders real backend candidates with the same UI it uses for sample data.
import type { CandidateOut } from "../../api/linkage";
import type { ColorKey, ConfLevel, Discovery, Evidence, Mention, Relative, TreeField } from "./data";

import { RECORD_TYPE_LABEL, roleLabel } from "./labels";

const SIGNAL_LABEL: Record<string, string> = {
  name: "NOMBRE", date: "FECHA", place: "LUGAR", relational: "FAMILIA",
};

function confLevel(score: number): ConfLevel {
  if (score >= 0.8) return "ALTA";
  if (score >= 0.6) return "MEDIA";
  return "BAJA";
}

function strength(value: number): { s: string; c: ColorKey } {
  if (value >= 0.8) return { s: "Fuerte", c: "ok" };
  if (value >= 0.5) return { s: "Media", c: "warn" };
  return { s: "Débil", c: "muted" };
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "··";
}

export function candidateToDiscovery(c: CandidateOut): Discovery {
  const tp = c.tree_person;
  const treeName = [tp?.given, tp?.surname].filter(Boolean).join(" ") || "(sin nombre)";
  const years = tp ? `${tp.birth_year ?? "?"}–${tp.death_year ?? ""}` : "";
  const rec = c.record;
  const isSibling = c.relation === "sibling";
  const recordLabel = RECORD_TYPE_LABEL[rec?.record_type ?? "other"] ?? "Acta";
  const pct = Math.round(c.score * 100);
  // for a sibling candidate the matched mention is the NEW sibling (the act's principal)
  const sibMention = isSibling ? (rec?.mentions ?? []).find((m) => m.id === c.person_mention_id) : null;
  const sibName = sibMention ? (sibMention.name_raw || [sibMention.given, sibMention.surname].filter(Boolean).join(" ")) : "";

  const mentions: Mention[] = (rec?.mentions ?? []).map((m) => ({
    role: roleLabel(m.role, rec?.record_type),
    name: m.name_raw || [m.given, m.surname].filter(Boolean).join(" ") || "—",
  }));

  const evidence: Evidence[] = Object.entries(c.evidence?.signals ?? {}).map(([k, sig]) => {
    const st = strength(sig.value);
    return { cat: SIGNAL_LABEL[k] ?? k.toUpperCase(), text: sig.reason, s: st.s, c: st.c };
  });

  // relatives = the act's other people (everything except the matched mention), addable on confirm
  const relatives: Relative[] = (rec?.mentions ?? [])
    .filter((m) => m.id !== c.person_mention_id)
    .map((m) => ({
      id: m.id,
      mentionId: m.id,
      name: m.name_raw || [m.given, m.surname].filter(Boolean).join(" ") || "—",
      rel: `${roleLabel(m.role, rec?.record_type)} · sugerido`,
    }));

  const treeFields: TreeField[] = [
    { k: "Nombre", v: treeName, c: "fg" },
    { k: "Años", v: years || "—", c: years ? "fg" : "muted" },
  ];

  return {
    id: c.id,
    candidateId: c.id,
    person: c.tree_person_id,
    cLevel: confLevel(c.score),
    pct,
    relation: c.relation,
    recTitle: `${isSibling ? "Posible hermano/a" : recordLabel}${rec?.date_year ? ` · ${rec.date_year}` : ""}${rec?.parish_raw ? ` · ${rec.parish_raw}` : ""}`,
    recRef: rec?.parish_raw || recordLabel,
    folio: rec?.folio_label ? `folio ${rec.folio_label}` : (rec?.page_no ? `pág. ${rec.page_no}` : ""),
    recQuote: rec?.summary || "(sin resumen)",
    docId: rec?.document_id ?? undefined,
    pageNo: rec?.page_no ?? undefined,
    treeName,
    treeMeta: years,
    treeInitial: initials(treeName),
    treeFields,
    mentions,
    evidence,
    yes: isSibling
      ? `Sí, es hermano/a${sibName ? ` (${sibName})` : ""} de ${tp?.given ?? treeName}`
      : `Sí, es ${tp?.given ?? treeName}`,
    relatives,
  };
}
