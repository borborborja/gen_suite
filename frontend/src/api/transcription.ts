import { api } from "./client";
import type { Job } from "./types";

export type { Job } from "./types";
export interface TranscriptionOut {
  id: string;
  page_no: number;
  engine: string;
  model: string | null;
  text: string | null;
  status: string;
}
export interface TranscriptionRevision extends TranscriptionOut {
  is_active: boolean; supersedes_id: string | null; reviewed_by: string | null; created_at: string;
}
export const getTranscriptionHistory = (docId: string, pageNo: number) =>
  api<TranscriptionRevision[]>(`/transcription/documents/${docId}/history?page_no=${pageNo}`);

export interface TranscribeOpts {
  engine?: string; model?: string; credential_id?: string; replace?: boolean;
}
export const startTranscription = (document_id: string, opts: TranscribeOpts = {}) =>
  api<Job>("/transcription/jobs", {
    method: "POST",
    body: JSON.stringify({ document_id, ...opts }),
  });
export const getJob = (id: string) => api<Job>(`/jobs/${id}`);
export const getTranscriptions = (docId: string) =>
  api<TranscriptionOut[]>(`/transcription/documents/${docId}`);

// Re-recognition versions: per page, the active transcription + the candidate from the last re-run.
export interface VersionPair {
  page_no: number;
  active: TranscriptionOut | null;
  candidate: TranscriptionOut | null;
}
export const getVersions = (docId: string) =>
  api<VersionPair[]>(`/transcription/documents/${docId}/versions`);

export interface ReconcileBody {
  mode: "substitute" | "mix" | "manual";
  criterion?: "frequency" | "llm";
  keep_history?: boolean;
  choices?: Record<string, string>; // page_no -> "old" | "new" | edited text
  expected_versions: Record<string, { active_id: string | null; candidate_id: string | null }>;
}
export const reconcile = (docId: string, body: ReconcileBody) =>
  api<{ pages: number }>(`/transcription/documents/${docId}/reconcile`, {
    method: "POST", body: JSON.stringify(body),
  });

// Human correction of an HTR transcription (eScriptorium-style loop).
export const correctTranscription = (id: string, text: string) =>
  api<TranscriptionOut>(`/transcription/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ text }),
  });

const TERMINAL = new Set(["completed", "partial", "error", "cancelled"]);
export const isTerminal = (s: string) => TERMINAL.has(s);
