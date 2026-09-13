import { api } from "./client";

export interface InteropConnection {
  id: string; provider: "gramps_desktop" | "gramps_web"; name: string; endpoint: string | null;
  status: string; last_pulled_at: string | null; last_pushed_at: string | null;
}

export interface SyncChange {
  id: string; connection_id: string; source_system: string; object_type: string; object_key: string;
  operation: "upsert" | "tombstone"; remote_revision: string | null; payload: Record<string, unknown> | null;
  state: string; conflict_with_id: string | null; resolution_note: string | null; resolved_at: string | null;
}

export interface ApplyResult { applied: number; deferred: number; tombstones: number }

export const listInteropConnections = () => api<InteropConnection[]>("/interop/connections");
export const listSyncChanges = (connectionId: string, state?: string) =>
  api<SyncChange[]>(`/interop/connections/${connectionId}/changes${state ? `?state=${state}` : ""}`);
export const applySyncChanges = (connectionId: string) =>
  api<ApplyResult>(`/interop/connections/${connectionId}/apply`, { method: "POST" });
export const resolveSyncChange = (changeId: string, resolution: "accept_remote" | "reject_remote", note?: string) =>
  api<SyncChange>(`/interop/changes/${changeId}/resolve`, { method: "POST", body: JSON.stringify({ resolution, note }) });
