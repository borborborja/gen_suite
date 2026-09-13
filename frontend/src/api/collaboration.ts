import { api } from "./client";

export interface Proposal {
  id: string; operation: string; patch: Record<string, unknown>; target_type: string | null;
  target_id: string | null; rationale: string | null; status: string; submitted_by: string | null;
  reviewed_by: string | null; reviewer_note: string | null; reviewed_at: string | null;
}
export interface Publication {
  id: string; title: string; slug: string; status: string; expires_at: string | null;
  published_at: string | null; share_path: string;
}
export interface CreatedPublication extends Publication { access_token: string }

export const listProposals = (status?: string) => api<Proposal[]>(`/proposals${status ? `?status=${status}` : ""}`);
export const createProposal = (body: { operation: string; patch: Record<string, unknown>; target_type?: string; target_id?: string; rationale?: string }) =>
  api<Proposal>("/proposals", { method: "POST", body: JSON.stringify(body) });
export const reviewProposal = (id: string, status: "accepted" | "rejected", reviewer_note?: string) =>
  api<Proposal>(`/proposals/${id}/review`, { method: "POST", body: JSON.stringify({ status, reviewer_note }) });
export const listPublications = () => api<Publication[]>("/publications");
export const createPublication = (body: { title: string; person_ids: string[]; expires_at?: string }) =>
  api<CreatedPublication>("/publications", { method: "POST", body: JSON.stringify(body) });
export const publishPublication = (id: string) => api<Publication>(`/publications/${id}/publish`, { method: "POST" });
export const refreshPublication = (id: string) => api<Publication>(`/publications/${id}/refresh`, { method: "POST" });
export const revokePublication = (id: string) => api<Publication>(`/publications/${id}/revoke`, { method: "POST" });
