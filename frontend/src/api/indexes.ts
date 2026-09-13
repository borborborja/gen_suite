import { api } from "./client";

export interface IndexSummary { id: string; title: string; status: string; purpose: string; is_active: boolean; version: number }
export interface IndexGeneration extends IndexSummary {
  include_public: boolean; profile: { engine: string; model: string | null; budget_policy?: Record<string, unknown> }; space_key: string;
  job_id: string | null; job_status?: string | null; research_run_id: string | null; error: string | null;
  counts: Record<string, number>; searchable: Record<string, number>; outdated: number;
}
export interface ActiveIndex { id: string | null; model: string | null; include_public: boolean }
export const listIndexes = (offset = 0) => api<IndexSummary[]>(`/search/indexes?offset=${offset}&limit=20`);
export const activeIndex = () => api<ActiveIndex>("/search/indexes/active");
export const getIndex = (id: string) => api<IndexGeneration>(`/search/indexes/${id}`);
export const createIndex = (body: { client_id: string; title: string; include_public: boolean; authorize: true }) =>
  api<IndexGeneration>("/search/indexes", { method: "POST", body: JSON.stringify(body) });
export const controlIndex = (id: string, action: string, body: { expected_version: number; expected_active_id: string | null; reason: string }) =>
  api<IndexGeneration>(`/search/indexes/${id}/${action}`, { method: "POST", body: JSON.stringify(body) });
