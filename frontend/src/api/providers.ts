import { api } from "./client";
import type { AiCall, Job } from "./types";

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
export const upsertBinding = (body: { task_type: string; credential_id: string; model?: string; params?: Record<string, unknown> }) =>
  api<Binding>("/providers/bindings", { method: "PUT", body: JSON.stringify(body) });

export interface Spend {
  month_cents: number; budget_cents: number | null; reserved_cents: number;
  settled_microusd: number; reserved_microusd: number; unknown_calls: number;
}
export const getSpend = () => api<Spend>("/providers/spend");
export const setBudget = (monthly_budget_cents: number | null) =>
  api<Spend>("/providers/budget", { method: "PUT", body: JSON.stringify({ monthly_budget_cents }) });
export const listAiCalls = (state: string) => api<AiCall[]>(`/providers/ai-calls?state=${encodeURIComponent(state)}&limit=50`);
export const reconcileAiCall = (id: string, body: { cost_microusd: number; evidence: string; provider_request_id: string | null }) =>
  api<AiCall>(`/providers/ai-calls/${id}/reconcile`, { method: "POST", body: JSON.stringify(body) });
export const recoverAiCall = (id: string) => api<AiCall>(`/providers/ai-calls/${id}/recover`, { method: "POST" });

// Legacy entry point: builds a separate generation; it never replaces the active index.
export const reembedCorpus = () => api<Job>("/providers/reembed-corpus", { method: "POST" });
export const getJob = (id: string) => api<Job>(`/jobs/${id}`);
