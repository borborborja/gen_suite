import { api } from "./client";

export interface ApiKey {
  id: string;
  name: string;
  scope: string;
  role: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface CreateApiKeyResponse {
  token: string; // shown once
  key: ApiKey;
}

export const listApiKeys = () => api<ApiKey[]>("/api-keys");

export const createApiKey = (body: { name: string; scope: "read" | "write"; expires_days?: number }) =>
  api<CreateApiKeyResponse>("/api-keys", { method: "POST", body: JSON.stringify(body) });

export const revokeApiKey = (id: string) =>
  api<void>(`/api-keys/${id}`, { method: "DELETE" });
