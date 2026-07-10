// Shared view-model types for the discovery queue (filled from the live linkage API via
// mapCandidate.ts) plus the keyboard-shortcut legend. The old sample corpus was removed —
// every view now renders real data or an honest empty state.

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

export const shortcuts = [
  { key: "S", label: "Sí, es la persona" },
  { key: "N", label: "No es la persona" },
  { key: "␣", label: "No lo sé" },
  { key: "Z", label: "Ampliar manuscrito" },
];
