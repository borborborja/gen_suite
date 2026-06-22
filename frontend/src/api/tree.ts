import { api } from "./client";

export interface TreePerson {
  id: string;
  given: string | null;
  surname: string | null;
  sex: string;
  birth_year: number | null;
  death_year: number | null;
  has_documents: boolean;
  deduction_count: number;
}
export interface TreeFamily {
  id: string;
  husband_id: string | null;
  wife_id: string | null;
  child_ids: string[];
}
export interface TreeGraph {
  focus: string;
  persons: TreePerson[];
  families: TreeFamily[];
}
export interface SearchHit {
  id: string;
  given: string | null;
  surname: string | null;
  birth_year: number | null;
  death_year: number | null;
}
export interface TreeStats {
  persons: number;
  families: number;
  events: number;
  places: number;
}
export interface Related {
  id: string;
  given: string | null;
  surname: string | null;
  sex: string;
  birth_year: number | null;
  death_year: number | null;
  relation: string | null;
}
export interface NameOut {
  type: string;
  given: string | null;
  surname: string | null;
  surname_prefix: string | null;
  nickname: string | null;
  is_primary: boolean;
  is_inferred: boolean;
}
export interface EventOut {
  id: string;
  type: string;
  date_raw: string | null;
  date_year: number | null;
  place: string | null;
  place_lat: number | null;
  place_lng: number | null;
  value: string | null;
  is_inferred: boolean;
}
export interface PersonDetail {
  id: string;
  sex: string;
  notes: string | null;
  names: NameOut[];
  events: EventOut[];
  parents: Related[];
  spouses: Related[];
  children: Related[];
  siblings: Related[];
}

export interface FactType { key: string; label: string }
export const getFactTypes = () => api<FactType[]>("/tree/fact-types");

export interface ResearchGap {
  kind: string;
  text: string;
  record_type: string;
  place: string | null;
  year_from: number | null;
  year_to: number | null;
  have_book: boolean;
  book_id: string | null;
  book_title: string | null;
  search: { given: string; surname: string; place: string; year_from: string; year_to: string };
}
export const getGaps = (id: string) => api<ResearchGap[]>(`/tree/persons/${id}/gaps`);

export interface CitationOut {
  id: string;
  note: string | null;
  target_type: string;
  document_id: string | null;
  document_title: string | null;
  page_no: number | null;
  record_type: string | null;
  date_raw: string | null;
  summary: string | null;
}
export const getCitations = (id: string) => api<CitationOut[]>(`/tree/persons/${id}/citations`);
export type EventBody = { type: string; date_raw?: string; place?: string; place_lat?: number; place_lng?: number; value?: string };
export const updatePerson = (id: string, body: { sex?: string; given?: string; surname?: string; surname_prefix?: string; nickname?: string; notes?: string }) =>
  api<PersonDetail>(`/tree/persons/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const addEvent = (id: string, body: EventBody) =>
  api<{ id: string }>(`/tree/persons/${id}/events`, { method: "POST", body: JSON.stringify(body) });
export const editEvent = (eventId: string, body: EventBody) =>
  api<{ id: string }>(`/tree/events/${eventId}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteEvent = (eventId: string) =>
  api<{ deleted: string }>(`/tree/events/${eventId}`, { method: "DELETE" });
export const addRelative = (id: string, body: { relation: string; relative_id?: string; given?: string; surname?: string; sex?: string }) =>
  api<{ id: string }>(`/tree/persons/${id}/relatives`, { method: "POST", body: JSON.stringify(body) });
export const unlinkRelative = (id: string, relativeId: string, relation: string) =>
  api<{ unlinked: string }>(`/tree/persons/${id}/relatives/${relativeId}?relation=${relation}`, { method: "DELETE" });
export const deletePerson = (id: string) =>
  api<{ deleted: string }>(`/tree/persons/${id}`, { method: "DELETE" });

export interface DuplicatePair { a: SearchHit; b: SearchHit; score: number; reason: string }
export const listDuplicates = () => api<DuplicatePair[]>("/tree/duplicates");
export const mergePersons = (keepId: string, dupId: string) =>
  api<PersonDetail>(`/tree/persons/${keepId}/merge`, { method: "POST", body: JSON.stringify({ dup_id: dupId }) });

export const geocodePlaces = (limit = 40) =>
  api<{ geocoded: number; remaining: number }>(`/tree/geocode-places?limit=${limit}`, { method: "POST" });

export const getHome = () => api<{ person_id: string | null }>("/tree/home");
export const setHome = (person_id: string) =>
  api<{ person_id: string | null }>("/tree/home", { method: "PUT", body: JSON.stringify({ person_id }) });

export const getStats = () => api<TreeStats>("/tree/stats");
export const getRoots = () => api<SearchHit[]>("/tree/roots?limit=200");
export const searchPersons = (
  q: string,
  filters?: { given?: string; surname?: string; year_from?: string; year_to?: string },
) => {
  const qs = new URLSearchParams();
  if (q && q.trim()) qs.set("q", q.trim());
  if (filters) for (const [k, v] of Object.entries(filters)) if (v) qs.set(k, v);
  return api<SearchHit[]>(`/tree/persons/search?${qs.toString()}`);
};
export const getSubtree = (id: string, depth = 3) =>
  api<TreeGraph>(`/tree/persons/${id}/subtree?depth=${depth}`);
export const getPerson = (id: string) => api<PersonDetail>(`/tree/persons/${id}`);

export async function importGedcom(file: File): Promise<any> {
  const fd = new FormData();
  fd.append("file", file);
  const token = localStorage.getItem("gs_access");
  const res = await fetch("/api/tree/import/gedcom", {
    method: "POST",
    body: fd,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error((await res.text()) || `${res.status}`);
  return res.json();
}

export async function downloadGedcom(): Promise<void> {
  const token = localStorage.getItem("gs_access");
  const res = await fetch("/api/tree/export/gedcom", {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "gen_suite_export.ged";
  a.click();
  URL.revokeObjectURL(url);
}

export function displayName(p: { given: string | null; surname: string | null }): string {
  return [p.given, p.surname].filter(Boolean).join(" ") || "(sin nombre)";
}

export function lifespan(p: { birth_year: number | null; death_year: number | null }): string {
  if (!p.birth_year && !p.death_year) return "";
  return `${p.birth_year ?? "?"}–${p.death_year ?? ""}`;
}
