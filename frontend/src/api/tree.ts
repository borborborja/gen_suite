import { api, authFetch } from "./client";

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
  family_id?: string | null;
  spouse_id?: string | null;
  spouse_name?: string | null;
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

export interface FactType { key: string; label: string; scope: "person" | "family" }
export const getFactTypes = () => api<FactType[]>("/tree/fact-types");

export interface FamilyOut {
  id: string;
  spouse: Related | null;
  children_count: number;
  events: EventOut[];
}
export const getPersonFamilies = (id: string) => api<FamilyOut[]>(`/tree/persons/${id}/families`);
export const addFamilyEvent = (familyId: string, body: EventBody) =>
  api<{ id: string }>(`/tree/families/${familyId}/events`, { method: "POST", body: JSON.stringify(body) });

export type CitationBody = { document_id?: string; page_no?: number; note?: string };
export const createCitation = (target_type: "person" | "event", target_id: string, body: CitationBody) =>
  api<{ id: string }>("/tree/citations", { method: "POST", body: JSON.stringify({ target_type, target_id, ...body }) });
export const updateCitation = (id: string, body: CitationBody) =>
  api<{ id: string }>(`/tree/citations/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteCitation = (id: string) =>
  api<{ deleted: string }>(`/tree/citations/${id}`, { method: "DELETE" });

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

export const createPerson = (body: { given?: string; surname?: string; sex?: string }) =>
  api<{ id: string }>("/tree/persons", { method: "POST", body: JSON.stringify(body) });

export interface PersonRow {
  id: string;
  given: string | null;
  surname: string | null;
  sex: string;
  birth_year: number | null;
  death_year: number | null;
}
export interface PersonPage { total: number; items: PersonRow[] }
export const listPersons = (opts: {
  q?: string; surname?: string; sort?: "name" | "birth" | "death";
  order?: "asc" | "desc"; page?: number; page_size?: number;
} = {}) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) if (v !== undefined && v !== "") qs.set(k, String(v));
  return api<PersonPage>(`/tree/persons?${qs.toString()}`);
};

export interface KinshipStep { person: SearchHit; step: string | null }
export interface RelationshipOut { related: boolean; label: string; path: KinshipStep[] }
export const getRelationship = (a: string, b: string) =>
  api<RelationshipOut>(`/tree/relationship?a=${a}&b=${b}`);

export interface PlaceRef { id: string; name: string; place_type: string | null }
export interface PlaceRow {
  id: string;
  name: string;
  place_type: string | null;
  parent_id: string | null;
  parent_name: string | null;
  lat: number | null;
  lng: number | null;
  event_count: number;
  children_count: number;
}
export interface PlacePage { total: number; items: PlaceRow[] }
export interface PlaceDetail extends PlaceRow { breadcrumb: PlaceRef[]; children: PlaceRef[] }
export interface PlaceEventRow {
  id: string; type: string; date_raw: string | null; date_year: number | null;
  person_id: string | null; person_name: string | null;
}

export const listPlaces = (opts: {
  q?: string; sort?: "name" | "events"; order?: "asc" | "desc"; page?: number; page_size?: number;
} = {}) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) if (v !== undefined && v !== "") qs.set(k, String(v));
  return api<PlacePage>(`/tree/places?${qs.toString()}`);
};
export const getPlace = (id: string) => api<PlaceDetail>(`/tree/places/${id}`);
export const listPlaceEvents = (id: string, page = 1) =>
  api<{ total: number; items: PlaceEventRow[] }>(`/tree/places/${id}/events?page=${page}`);
export const patchPlace = (id: string, body: {
  name?: string; place_type?: string; parent_id?: string; clear_parent?: boolean;
  lat?: number; lng?: number;
}) => api<{ id: string }>(`/tree/places/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const mergePlace = (id: string, intoId: string) =>
  api<{ id: string }>(`/tree/places/${id}/merge`, { method: "POST", body: JSON.stringify({ into_id: intoId }) });
export const geocodePlace = (id: string) =>
  api<{ id: string; lat: number; lng: number }>(`/tree/places/${id}/geocode`, { method: "POST" });

export interface ChangeItem {
  id: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  summary: string | null;
  actor_email: string | null;
  created_at: string;
  reverted_at: string | null;
  revert_of: string | null;
  rows_count: number;
}
export interface ChangeRowImage { table: string; pk: Record<string, unknown>; before: Record<string, unknown> | null; after: Record<string, unknown> | null }
export interface ChangeDetail extends ChangeItem { rows: ChangeRowImage[] }

export const listChanges = (page = 1, pageSize = 50) =>
  api<{ total: number; items: ChangeItem[] }>(`/tree/changes?page=${page}&page_size=${pageSize}`);
export const getChange = (id: string) => api<ChangeDetail>(`/tree/changes/${id}`);
export const revertChange = (id: string) =>
  api<{ reverted: string }>(`/tree/changes/${id}/revert`, { method: "POST" });

export const PLACE_TYPE_LABEL: Record<string, string> = {
  country: "País", region: "Región", province: "Provincia",
  municipality: "Municipio", parish: "Parroquia", other: "Otro",
};

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

export interface GedcomImportResult {
  individuals: number;
  families: number;
  places: number;
  [k: string]: unknown;
}

export async function importGedcom(file: File): Promise<GedcomImportResult> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await authFetch("/tree/import/gedcom", { method: "POST", body: fd });
  if (!res.ok) throw new Error((await res.text()) || `${res.status}`);
  return res.json();
}

export async function downloadGedcom(): Promise<void> {
  const res = await authFetch("/tree/export/gedcom");
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
