import { api } from "./client";

export interface RecordHit {
  record_id: string;
  mention_id: string | null;
  document_id: string;
  document_title: string | null;
  page_no: number | null;
  record_type: string;
  date_raw: string | null;
  date_year: number | null;
  place: string | null;
  given: string | null;
  surname: string | null;
  role: string | null;
  summary: string | null;
  score: number;
}

export interface RecordFilters {
  q?: string;
  given?: string;
  surname?: string;
  record_type?: string;
  place?: string;
  year_from?: string;
  year_to?: string;
  role?: string;
  document_id?: string;
  semantic?: boolean;
  fuzzy?: boolean;
}

export function searchRecords(f: RecordFilters, limit = 40) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === "") continue;
    if (v === false && k !== "fuzzy") continue; // keep fuzzy=false explicit
    qs.set(k, String(v));
  }
  qs.set("limit", String(limit));
  return api<RecordHit[]>(`/search/records?${qs.toString()}`);
}

export interface Suggestion { value: string; count: number; score: number }
export const suggest = (field: "surname" | "given" | "place", q: string) =>
  api<Suggestion[]>(`/search/suggest?field=${field}&q=${encodeURIComponent(q)}`);
