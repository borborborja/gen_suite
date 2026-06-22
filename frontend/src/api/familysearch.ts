import { api } from "./client";

export interface FsCredential { id: string; label: string; is_active: boolean }

export const startFsDownload = (body: { url: string; max_images?: number; delay?: number }) =>
  api<{ id: string; status: string }>("/connectors/familysearch/jobs", { method: "POST", body: JSON.stringify(body) });

export const listFsCredentials = () => api<FsCredential[]>("/connectors/familysearch/credentials");

export const addFsCredential = (body: { label: string; cookies_json: string }) =>
  api<{ id: string; label: string }>("/connectors/familysearch/credentials", { method: "POST", body: JSON.stringify(body) });

export const deleteFsCredential = (id: string) =>
  api<void>(`/connectors/familysearch/credentials/${id}`, { method: "DELETE" });
