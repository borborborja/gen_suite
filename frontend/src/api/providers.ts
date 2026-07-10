import { api } from "./client";
import type { Job } from "./types";

export interface CatalogEntry {
  key: string;
  display_name: string;
  capabilities: string[];
  default_base_url: string | null;
  default_model: string | null;
  requires_key: boolean;
}
export interface Credential {
  id: string;
  scope: string;
  tenant_id: string | null;
  provider_key: string;
  label: string;
  base_url: string | null;
  model_default: string | null;
  key_masked: string | null;
  is_active: boolean;
  created_at: string;
}
export interface Binding {
  id: string;
  task_type: string;
  credential_id: string;
  model: string | null;
  params: Record<string, unknown> | null;
}

export const getCatalog = () => api<CatalogEntry[]>("/providers/catalog");
export const listCredentials = () => api<Credential[]>("/providers/credentials");
export const createCredential = (body: {
  scope: string;
  provider_key: string;
  label: string;
  api_key?: string;
  base_url?: string;
  model_default?: string;
}) => api<Credential>("/providers/credentials", { method: "POST", body: JSON.stringify(body) });
export const deleteCredential = (id: string) =>
  api(`/providers/credentials/${id}`, { method: "DELETE" });
export const listBindings = () => api<Binding[]>("/providers/bindings");
export const upsertBinding = (body: { task_type: string; credential_id: string; model?: string }) =>
  api<Binding>("/providers/bindings", { method: "PUT", body: JSON.stringify(body) });

export interface Spend { month_cents: number; budget_cents: number | null }
export const getSpend = () => api<Spend>("/providers/spend");
export const setBudget = (monthly_budget_cents: number | null) =>
  api<Spend>("/providers/budget", { method: "PUT", body: JSON.stringify({ monthly_budget_cents }) });

// Re-embed the whole corpus with the active embedding model (run after switching providers —
// vectors from different models aren't comparable).
export const reembedCorpus = () => api<Job>("/providers/reembed-corpus", { method: "POST" });
export const getJob = (id: string) => api<Job>(`/jobs/${id}`);
